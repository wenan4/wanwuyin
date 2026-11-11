"""Contract tests: no credentials, internet, GLB assets, or optional packages needed."""
import base64
import copy
import io
import json
import struct
import sys
import tempfile
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys import lux3d  # noqa: E402
from wwys.seal_api import MAX_BODY_BYTES, make_server  # noqa: E402
from wwys.seal_jobs import JobStore, RequestError, SealService, validate_request  # noqa: E402
from wwys.seal_meta import upsert, write_seals  # noqa: E402

PNG = b"\x89PNG\r\n\x1a\nexample-image"
GLB = b"glTF" + struct.pack("<II", 2, 12)
TEXT = {"label": "大象", "why": "太大", "real": "身长约六米", "prompt": "一只完整的大象，白色背景"}
SECRET = "a-test-credential-that-must-never-appear"


def fake_metadata(path, label):
    return {"cells": "⠙⠔⠓⠭", "yaw": 0.12}


def wait_for(predicate, timeout=8):
    until = time.monotonic() + timeout
    while time.monotonic() < until:
        result = predicate()
        if result:
            return result
        time.sleep(0.01)
    raise AssertionError("Timed out waiting for task state")


class FakeLux3D:
    result_urls = staticmethod(lux3d.result_urls)

    def __init__(self, ready=True):
        self.lock = threading.Lock()
        self.ready = threading.Event()
        if ready:
            self.ready.set()
        self.created, self.uploads = [], []
        self.active = set()
        self.max_active = 0
        self.costs = {"text": 13, "image": 1}
        self.create_error = None
        self.concurrency_rejections = 0
        self.poll_errors = 0
        self.remote_status = 3
        self.bad_download = False
        self.download_count = 0

    def upload(self, path):
        self.uploads.append(Path(path).read_bytes())
        return "https://assets.example/input.png"

    def balance(self):
        return {"uniqueId": "account", "availableCredits": 447}

    def quote(self, kind, payload, unique_id):
        return {"quoteId": "quote", "estimatedCreditsTotal": self.costs[kind]}

    def create(self, kind, payload, quote_id):
        with self.lock:
            if self.concurrency_rejections:
                self.concurrency_rejections -= 1
                raise lux3d.Lux3DError("GENERATION_CONCURRENCY_LIMIT_EXCEEDED")
            self.created.append((kind, copy.deepcopy(payload), quote_id))
            if self.create_error:
                raise self.create_error
            remote_id = str(len(self.created))
            self.active.add(remote_id)
            self.max_active = max(self.max_active, len(self.active))
            return remote_id

    def get_task(self, remote_id):
        with self.lock:
            if self.poll_errors:
                self.poll_errors -= 1
                raise ConnectionError(SECRET)
            if not self.ready.is_set():
                return {"status": 1}
            self.active.discard(remote_id)
            return {"status": self.remote_status, "outputs": [{"content": "https://assets.example/model.glb?token=" + SECRET}]}

    def download(self, url, dest, max_bytes=None):
        self.download_count += 1
        Path(dest).write_bytes(b"broken" if self.bad_download else GLB)


class ServiceFixture(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.client = FakeLux3D()

    def start(self, **options):
        defaults = {"client": self.client, "make_metadata": fake_metadata, "poll_interval": 0.02}
        defaults.update(options)
        service = SealService(self.root / "state", self.root / "public", **defaults)
        self.addCleanup(service.close)
        return service

    def done(self, service, job_id):
        return wait_for(lambda: (job := service.get(job_id))["status"] in {"completed", "failed"} and job)


class ServiceTests(ServiceFixture):
    def test_text_and_image_publish_the_viewer_contract(self):
        service = self.start()
        text, _ = service.submit(TEXT)
        image_request = {k: v for k, v in TEXT.items() if k != "prompt"}
        image_request["image"] = {"filename": "../../ignored.jpg", "base64": base64.b64encode(PNG).decode()}
        image, _ = service.submit(image_request)
        for job in (text, image):
            result = self.done(service, job["id"])
            self.assertEqual(result["status"], "completed")
            self.assertEqual(set(result["seal"]), {"name", "label", "cells", "why", "real", "glb", "yaw"})
            self.assertTrue((self.root / "public" / result["seal"]["glb"]).is_file())
            self.assertEqual(result["seal"]["name"], "seal_" + job["id"])
        wait_for(lambda: len(json.loads((self.root / "public/generated.json").read_text("utf-8"))) == 2)
        self.assertFalse((self.root / "public/seals.json").exists())
        self.assertEqual(self.client.uploads, [PNG])
        calls = {kind: payload for kind, payload, quote in self.client.created}
        self.assertTrue(calls["text"]["enablePbr"])
        self.assertEqual(calls["text"]["style"], "photorealistic")
        self.assertEqual(calls["image"]["img"], "https://assets.example/input.png")

    def test_sixth_task_waits_and_all_six_complete(self):
        self.client.ready.clear()
        service = self.start()
        jobs = [service.submit(TEXT)[0] for _ in range(6)]
        wait_for(lambda: len(self.client.created) == 5)
        sixth = service.get(jobs[5]["id"])
        self.assertEqual(sixth["status"], "queued")
        self.assertEqual(sixth["queuePosition"], 1)
        self.assertEqual(self.client.max_active, 5)
        self.client.ready.set()
        for job in jobs:
            self.assertEqual(self.done(service, job["id"])["status"], "completed")
        self.assertLessEqual(self.client.max_active, 5)

    def test_running_and_queued_jobs_resume_without_another_create(self):
        self.client.ready.clear()
        service = self.start(workers=1)
        first, _ = service.submit(TEXT)
        second, _ = service.submit(TEXT)
        wait_for(lambda: service.get(first["id"])["status"] == "generating")
        service.close()
        self.assertEqual(len(self.client.created), 1)
        resumed = self.start(workers=1)
        self.client.ready.set()
        for job in (first, second):
            self.assertEqual(self.done(resumed, job["id"])["status"], "completed")
        self.assertEqual(len(self.client.created), 2)

    def test_quote_limit_does_not_create_or_reserve(self):
        self.client.costs["text"] = 16
        service = self.start()
        job, _ = service.submit(TEXT)
        result = self.done(service, job["id"])
        self.assertEqual(result["error"]["code"], "QUOTE_LIMIT")
        self.assertEqual(self.client.created, [])
        self.assertEqual(service.store.get(job["id"])["reserved"], 0)

    def test_daily_budget_is_atomic_across_workers_and_persists(self):
        service = self.start(daily_budget=26)
        jobs = [service.submit(TEXT)[0] for _ in range(12)]
        results = [self.done(service, job["id"]) for job in jobs]
        self.assertEqual(sum(r["status"] == "completed" for r in results), 2)
        self.assertEqual(sum(bool(r["error"] and r["error"]["code"] == "DAILY_BUDGET") for r in results), 10)
        service.close()
        resumed = self.start(daily_budget=26)
        extra, _ = resumed.submit(TEXT)
        self.assertEqual(self.done(resumed, extra["id"])["error"]["code"], "DAILY_BUDGET")
        self.assertEqual(len(self.client.created), 2)

    def test_concurrent_idempotency_keys_prevent_duplicate_charges(self):
        service = self.start()
        with ThreadPoolExecutor(max_workers=10) as pool:
            responses = list(pool.map(lambda _: service.submit(TEXT, "same-request"), range(10)))
        self.assertEqual(sum(created for job, created in responses), 1)
        ids = {job["id"] for job, created in responses}
        self.assertEqual(len(ids), 1)
        self.done(service, ids.pop())
        self.assertEqual(len(self.client.created), 1)
        with self.assertRaises(RequestError) as caught:
            service.submit(dict(TEXT, label="蓝鲸"), "same-request")
        self.assertEqual(caught.exception.status, 409)
        service.close()
        resumed = self.start()
        _, created = resumed.submit(TEXT, "same-request")
        self.assertFalse(created)
        self.assertEqual(len(self.client.created), 1)

    def test_upstream_concurrency_limit_requeues_without_reserving_twice(self):
        self.client.concurrency_rejections = 1
        service = self.start(daily_budget=13)
        job, _ = service.submit(TEXT)
        self.assertEqual(self.done(service, job["id"])["status"], "completed")
        self.assertEqual(len(self.client.created), 1)
        self.assertEqual(service.store.get(job["id"])["reserved"], 13)

    def test_ambiguous_create_fails_closed_and_never_retries(self):
        self.client.create_error = ConnectionError(SECRET)
        service = self.start()
        with self.assertLogs("wwys.seal_jobs", level="WARNING") as logs:
            job, _ = service.submit(TEXT, "ambiguous")
            result = self.done(service, job["id"])
        self.assertEqual(result["error"]["code"], "CREATE_UNCERTAIN")
        self.assertNotIn(SECRET, json.dumps(result) + "".join(logs.output))
        service.close()
        resumed = self.start()
        self.assertEqual(resumed.get(job["id"])["status"], "failed")
        self.assertEqual(resumed.store.get(job["id"])["reserved"], 13)
        self.assertEqual(len(self.client.created), 1)

    def test_crash_during_create_preserves_reservation_without_creating_again(self):
        state = self.root / "state"
        state.mkdir()
        store = JobStore(state / "jobs.sqlite3")
        values, _ = validate_request(TEXT)
        job_id = "a" * 32
        store.insert(job_id, values, None, "fingerprint", 100)
        self.assertTrue(store.reserve(job_id, 13, 100))
        service = self.start()
        result = service.get(job_id)
        self.assertEqual(result["error"]["code"], "CREATE_UNCERTAIN")
        self.assertEqual(service.store.get(job_id)["reserved"], 13)
        self.assertEqual(self.client.created, [])

    def test_polling_disconnects_reuse_the_saved_remote_id(self):
        self.client.poll_errors = 2
        service = self.start()
        with self.assertLogs("wwys.seal_jobs", level="WARNING") as logs:
            job, _ = service.submit(TEXT)
            result = self.done(service, job["id"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(len(self.client.created), 1)
        self.assertNotIn(SECRET, json.dumps(result) + "".join(logs.output))

    def test_processing_failure_can_resume_without_regeneration(self):
        self.client.bad_download = True
        service = self.start()
        job, _ = service.submit(TEXT)
        self.assertEqual(self.done(service, job["id"])["error"]["code"], "PROCESSING_FAILED")
        self.assertEqual(list((self.root / "public").rglob("*.part")), [])
        self.client.bad_download = False
        service.retry_processing(job["id"])
        self.assertEqual(self.done(service, job["id"])["status"], "completed")
        self.assertEqual(len(self.client.created), 1)

    def test_restart_after_download_reuses_glb_even_if_output_urls_expired(self):
        def broken_metadata(*args):
            raise RuntimeError(SECRET)

        service = self.start(make_metadata=broken_metadata)
        job, _ = service.submit(TEXT)
        self.assertEqual(self.done(service, job["id"])["status"], "failed")
        self.assertEqual(self.client.download_count, 1)
        service.close()
        resumed = self.start()
        resumed.retry_processing(job["id"])
        self.assertEqual(self.done(resumed, job["id"])["status"], "completed")
        self.assertEqual(self.client.download_count, 1)
        self.assertEqual(len(self.client.created), 1)

    def test_upstream_failed_status_is_terminal_and_sanitized(self):
        self.client.remote_status = 4
        service = self.start()
        job, _ = service.submit(TEXT)
        result = self.done(service, job["id"])
        self.assertEqual(result["error"]["code"], "GENERATION_FAILED")
        self.assertNotIn(SECRET, json.dumps(result))

    def test_second_worker_pool_is_rejected(self):
        self.start()
        with self.assertRaisesRegex(RuntimeError, "Another seal worker"):
            self.start()

    def test_queue_is_bounded(self):
        self.client.ready.clear()
        service = self.start(workers=1, max_pending=1)
        service.submit(TEXT)
        with self.assertRaises(RequestError) as caught:
            service.submit(TEXT)
        self.assertEqual(caught.exception.status, 429)


class HTTPTests(ServiceFixture):
    def setUp(self):
        super().setUp()
        self.service = self.start()
        self.http = make_server(self.service, port=0, token="test-token", origins=["http://localhost:5199"])
        self.thread = threading.Thread(target=self.http.serve_forever, kwargs={"poll_interval": 0.02}, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_http)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        self.opener = build_opener(ProxyHandler({}))

    def stop_http(self):
        self.http.shutdown()
        self.http.server_close()
        self.thread.join()

    def request(self, path, body=None, headers=None, method=None):
        hdrs = {"Authorization": "Bearer test-token"}
        if isinstance(body, dict):
            body = json.dumps(body).encode()
            hdrs["Content-Type"] = "application/json"
        hdrs.update(headers or {})
        req = Request(self.url + path, data=body, headers=hdrs, method=method)
        try:
            response = self.opener.open(req, timeout=5)
        except HTTPError as e:
            response = e
        with response:
            raw = response.read()
            return response.code, json.loads(raw) if "json" in response.headers.get("Content-Type", "") else raw

    def test_http_create_poll_manifest_and_download(self):
        status, job = self.request("/api/seals", TEXT, {"Idempotency-Key": "http-task", "Origin": "http://localhost:5199"})
        self.assertEqual(status, 202)
        done = self.done(self.service, job["id"])
        status, duplicate = self.request("/api/seals", TEXT, {"Idempotency-Key": "http-task"})
        self.assertEqual(status, 200)
        self.assertEqual(duplicate["id"], job["id"])
        self.assertEqual(self.request("/api/seals/" + job["id"])[1]["status"], "completed")
        self.assertEqual(len(self.request("/api/seals")[1]["seals"]), 1)
        self.assertEqual(len(self.request("/generated.json")[1]), 1)
        self.assertEqual(self.request("/" + done["seal"]["glb"]), (200, GLB))

    def test_http_rejects_bad_requests_and_protects_private_files(self):
        cases = [
            ("/api/health", None, {"Authorization": ""}, 401),
            ("/api/seals", TEXT, {"Origin": "https://attacker.example"}, 403),
            ("/api/seals", b"not-json", {"Content-Type": "application/json"}, 400),
            ("/api/seals", {"label": "大象"}, {}, 400),
            ("/api/seals", TEXT, {"Content-Length": str(MAX_BODY_BYTES + 1)}, 413),
            ("/api/seals", b"x", {"Content-Type": "text/plain"}, 415),
            ("/api/seals/" + "f" * 32, None, {}, 404),
            ("/objects/../../state/jobs.sqlite3", None, {}, 404),
            ("/.env", None, {}, 404),
        ]
        for path, body, headers, expected in cases:
            with self.subTest(path=path, expected=expected):
                status, response = self.request(path, body, headers)
                self.assertEqual(status, expected)
                self.assertNotIn(SECRET, json.dumps(response))

    def test_multipart_image_upload_ignores_untrusted_filename(self):
        boundary = "wanwuyin-test-boundary"
        parts = []
        for key in ("label", "why", "real"):
            parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{key}"\r\n\r\n{TEXT[key]}\r\n'.encode())
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="image"; filename="../../outside.png"\r\nContent-Type: image/png\r\n\r\n'.encode() + PNG + b"\r\n")
        parts.append(f"--{boundary}--\r\n".encode())
        status, job = self.request("/api/seals", b"".join(parts), {"Content-Type": f"multipart/form-data; boundary={boundary}"})
        self.assertEqual(status, 202)
        self.assertEqual(self.done(self.service, job["id"])["status"], "completed")
        self.assertEqual(self.client.uploads, [PNG])
        self.assertFalse((self.root / "outside.png").exists())


class InputAndAssetTests(unittest.TestCase):
    def test_manifest_retries_brief_windows_reader_conflicts(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "generated.json"
            write_seals(path, [])
            import os
            replace = os.replace
            attempts = []

            def busy_twice(source, target):
                attempts.append(1)
                if len(attempts) <= 2:
                    raise PermissionError("temporary reader lock")
                return replace(source, target)

            with patch("wwys.seal_meta.os.replace", side_effect=busy_twice):
                write_seals(path, [{"name": "seal_test"}])
            self.assertEqual(json.loads(path.read_text("utf-8")), [{"name": "seal_test"}])
            self.assertEqual(len(attempts), 3)

    def test_rejects_ambiguous_inputs_remote_urls_html_and_invalid_images(self):
        bad = [None, [], dict(TEXT, label="<img>"), dict(TEXT, label="恐龙2"),
               dict(TEXT, why=""), dict(TEXT, image=base64.b64encode(PNG).decode()),
               dict(TEXT, prompt=""), dict(TEXT, prompt=None, image="https://example/image.png"),
               dict(TEXT, prompt=None, image="not-base64"), dict(TEXT, prompt=None, image="eA==")]
        for data in bad:
            with self.subTest(data=data), self.assertRaises(RequestError):
                validate_request(data)

    def test_manifest_helper_initializes_and_updates_without_path_traversal(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            glb = root / "source.glb"
            glb.write_bytes(GLB)
            path = root / "public/seals.json"
            upsert(path, "elephant", glb, fake_metadata(None, None), label="大象", why="太大", real="六米")
            upsert(path, "elephant", glb, {"yaw": 0.5})
            entries = json.loads(path.read_text("utf-8"))
            self.assertEqual(len(entries), 1)
            self.assertEqual(entries[0]["yaw"], 0.5)
            self.assertEqual(entries[0]["cells"], "⠙⠔⠓⠭")
            with self.assertRaises(ValueError):
                upsert(path, "../../outside", glb, {"yaw": 0})
            self.assertEqual(list(root.rglob(".seals.json.*")), [])

    def test_download_enforces_a_byte_limit(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch("wwys.lux3d.urllib.request.urlopen", return_value=io.BytesIO(b"0123456789")):
                with self.assertRaises(lux3d.Lux3DError):
                    lux3d.download("https://example/model.glb", Path(directory) / "model.glb", max_bytes=5)


if __name__ == "__main__":
    unittest.main()
