"""Durable, bounded Lux3D task workers. This module itself only needs the stdlib."""
import base64
import binascii
import hashlib
import json
import logging
import math
import os
import queue
import re
import sqlite3
import threading
import time
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

from . import lux3d
from .seal_meta import write_seals

LOG = logging.getLogger(__name__)
CST = timezone(timedelta(hours=8))
MAX_IMAGE_BYTES = 8 * 1024 * 1024
TERMINAL = {"completed", "failed"}
ERRORS = {
    "QUOTE_LIMIT": "报价超过单次积分上限，未创建生成任务。",
    "DAILY_BUDGET": "今日积分预算不足，未创建生成任务。",
    "UPSTREAM_ERROR": "生成服务暂时不可用，请稍后重新提交。",
    "GENERATION_FAILED": "模型生成失败，请修改描述或照片后重新提交。",
    "CREATE_UNCERTAIN": "创建结果尚不能确认，已停止自动重试以避免重复扣费。请由管理员核对上游任务。",
    "PROCESSING_FAILED": "模型处理失败；修复运行环境后可用原任务号重试处理，无需重新生成。",
}


class RequestError(ValueError):
    def __init__(self, message, status=400, code="INVALID_REQUEST"):
        super().__init__(message)
        self.status, self.code = status, code


def image_suffix(data):
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return ".png"
    if data.startswith(b"\xff\xd8\xff"):
        return ".jpg"
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return ".webp"
    raise RequestError("照片必须为 PNG、JPEG 或 WebP。")


def validate_request(data):
    if not isinstance(data, dict):
        raise RequestError("请求体必须为对象。")
    values = {}
    for key, limit in (("label", 40), ("why", 100), ("real", 100)):
        value = data.get(key)
        if not isinstance(value, str) or not value.strip() or len(value) > limit:
            raise RequestError(f"{key} 必填，最多 {limit} 个字符。")
        value = value.strip()
        if any(ord(c) < 32 or c in "<>" for c in value):
            raise RequestError(f"{key} 不可包含控制字符或 HTML。")
        values[key] = value
    # seal.ts currently renders labels as HTML. Restrict labels before publication.
    if any(c.isdigit() for c in values["label"]):
        raise RequestError("名字里的数字请写成汉字，数字盲文规则尚待确认。")
    prompt, image = data.get("prompt"), data.get("image")
    if (isinstance(prompt, str) and bool(prompt.strip())) == (image is not None):
        raise RequestError("请提供 prompt 或 image，且只能选择一种输入。")
    blob = None
    if image is not None:
        if isinstance(image, bytes):  # multipart/form-data
            blob = image
        else:
            encoded = image.get("base64") if isinstance(image, dict) else image
            if not isinstance(encoded, str) or len(encoded) > (MAX_IMAGE_BYTES * 4 // 3 + 1024):
                raise RequestError("image 必须是 base64 编码的照片，最大 8 MiB。")
            if encoded.startswith("data:"):
                if ";base64," not in encoded:
                    raise RequestError("照片 data URL 必须使用 base64。")
                encoded = encoded.split(";base64,", 1)[1]
            try:
                blob = base64.b64decode(encoded, validate=True)
            except (ValueError, binascii.Error):
                raise RequestError("照片的 base64 编码无效。") from None
        if not blob or len(blob) > MAX_IMAGE_BYTES:
            raise RequestError("照片最大 8 MiB。", 413)
        values.update(kind="image", image_suffix=image_suffix(blob),
                      image_hash=hashlib.sha256(blob).hexdigest())
    else:
        if not isinstance(prompt, str) or len(prompt) > 2000:
            raise RequestError("prompt 最多 2000 个字符。")
        values.update(kind="text", prompt=prompt.strip())
    return values, blob


class InstanceLock:
    """One worker pool per state directory, including on Windows."""
    def __init__(self, path):
        self.file = open(path, "a+b")
        self.file.seek(0, os.SEEK_END)
        if self.file.tell() == 0:
            self.file.write(b"0")
            self.file.flush()
        self.file.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(self.file.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.file.close()
            raise RuntimeError("Another seal worker already uses this state directory") from None

    def close(self):
        self.file.close()


class JobStore:
    def __init__(self, path):
        self.path = path
        with self.connect() as db:
            db.execute("PRAGMA journal_mode=WAL")
            db.execute("""CREATE TABLE IF NOT EXISTS jobs (
                id TEXT PRIMARY KEY, idem_key TEXT UNIQUE, fingerprint TEXT NOT NULL,
                request TEXT NOT NULL, status TEXT NOT NULL,
                created REAL NOT NULL, updated REAL NOT NULL, remote_id TEXT,
                quote REAL, reserved REAL NOT NULL DEFAULT 0, budget_day TEXT,
                result TEXT, error_code TEXT)""")

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA synchronous=FULL")
        try:
            with db:
                yield db
        finally:
            db.close()

    def get(self, job_id):
        with self.connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return dict(row) if row else None

    def insert(self, job_id, request, key, fingerprint, max_pending):
        now = time.time()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if key:
                old = db.execute("SELECT * FROM jobs WHERE idem_key=?", (key,)).fetchone()
                if old:
                    if old["fingerprint"] != fingerprint:
                        raise RequestError("同一 Idempotency-Key 不可提交不同内容。", 409, "IDEMPOTENCY_CONFLICT")
                    return dict(old), False
            count = db.execute("SELECT COUNT(*) FROM jobs WHERE status NOT IN ('completed','failed')").fetchone()[0]
            if count >= max_pending:
                raise RequestError("等待任务较多，请稍后再试。", 429, "QUEUE_FULL")
            db.execute("""INSERT INTO jobs (id,idem_key,fingerprint,request,status,created,updated)
                          VALUES (?,?,?,?, 'queued',?,?)""",
                       (job_id, key, fingerprint, json.dumps(request, ensure_ascii=False), now, now))
        return self.get(job_id), True

    def update(self, job_id, **fields):
        fields["updated"] = time.time()
        with self.connect() as db:
            db.execute("UPDATE jobs SET " + ",".join(f"{k}=?" for k in fields) + " WHERE id=?",
                       (*fields.values(), job_id))

    def reserve(self, job_id, credits, cap):
        day = datetime.now(CST).date().isoformat()
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            spent = db.execute("SELECT COALESCE(SUM(reserved),0) FROM jobs WHERE budget_day=?", (day,)).fetchone()[0]
            if spent + credits > cap:
                return False
            # Persist the uncertain-create marker BEFORE the non-idempotent HTTP call.
            db.execute("UPDATE jobs SET reserved=?,budget_day=?,quote=?,status='creating',updated=? WHERE id=?",
                       (credits, day, credits, time.time(), job_id))
        return True

    def recover(self):
        with self.connect() as db:
            db.execute("UPDATE jobs SET status='failed',error_code='CREATE_UNCERTAIN',updated=? WHERE status='creating' AND remote_id IS NULL", (time.time(),))
            db.execute("UPDATE jobs SET status='queued',updated=? WHERE status='quoting'", (time.time(),))
            rows = db.execute("SELECT id FROM jobs WHERE status NOT IN ('completed','failed') ORDER BY remote_id IS NULL, created").fetchall()
            return [r[0] for r in rows]

    def results(self):
        with self.connect() as db:
            return [json.loads(r[0]) for r in db.execute("SELECT result FROM jobs WHERE status='completed' ORDER BY created")]

    def queue_position(self, job):
        if job["status"] != "queued":
            return 0
        with self.connect() as db:
            return db.execute("SELECT COUNT(*) FROM jobs WHERE status='queued' AND created<=?", (job["created"],)).fetchone()[0]


class SealService:
    def __init__(self, state_dir, public_dir, *, client=lux3d, make_metadata=None,
                 workers=5, daily_budget=100, text_limit=15, image_limit=3,
                 poll_interval=5, max_pending=100):
        if not 1 <= workers <= 5:
            raise ValueError("workers must be between 1 and 5")
        if any(not math.isfinite(x) or x <= 0 for x in (daily_budget, text_limit, image_limit, poll_interval)):
            raise ValueError("budgets and poll interval must be positive finite numbers")
        if max_pending < workers:
            raise ValueError("max_pending must be at least workers")
        if make_metadata is None:
            from .seal_meta import check_runtime, metadata
            check_runtime()
            make_metadata = metadata
        self.state_dir, self.public_dir = Path(state_dir).resolve(), Path(public_dir).resolve()
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.public_dir.mkdir(parents=True, exist_ok=True)
        if self.state_dir.is_relative_to(self.public_dir):
            raise ValueError("state_dir must be outside public_dir")
        self.lock = InstanceLock(self.state_dir / "service.lock")
        self.client, self.make_metadata = client, make_metadata
        self.daily_budget = daily_budget
        self.limits = {"text": text_limit, "image": image_limit}
        self.poll_interval, self.max_pending = poll_interval, max_pending
        self.store = JobStore(self.state_dir / "jobs.sqlite3")
        self.queue = queue.Queue()
        self.stop = threading.Event()
        self.manifest_lock = threading.Lock()
        self.enqueued, self.enqueue_lock = set(), threading.Lock()
        self.threads = []
        try:
            pending = self.store.recover()
            self.publish_manifest()
            for job_id in pending:
                self.enqueue(job_id)
            self.threads = [threading.Thread(target=self.worker, daemon=True, name=f"seal-{i}") for i in range(workers)]
            for t in self.threads:
                t.start()
        except BaseException:
            self.stop.set()
            self.lock.close()
            raise

    def enqueue(self, job_id):
        with self.enqueue_lock:
            if job_id not in self.enqueued:
                self.enqueued.add(job_id)
                self.queue.put(job_id)

    def submit(self, data, key=None):
        if key is not None and not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", key):
            raise RequestError("Idempotency-Key 需为 1–128 个 ASCII 字母、数字或 _ . : -。")
        values, image = validate_request(data)
        fingerprint = hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        job_id = uuid.uuid4().hex
        image_path = None
        if image:
            upload_dir = self.state_dir / "uploads"
            upload_dir.mkdir(exist_ok=True)
            image_path = upload_dir / (job_id + values["image_suffix"])
            image_path.write_bytes(image)
            values["image_file"] = image_path.name
        try:
            job, created = self.store.insert(job_id, values, key, fingerprint, self.max_pending)
        except BaseException:
            if image_path:
                image_path.unlink(missing_ok=True)
            raise
        if not created and image_path:
            image_path.unlink(missing_ok=True)
        if created:
            self.enqueue(job["id"])
        return self.public_job(job), created

    def public_job(self, job):
        if not job:
            raise RequestError("没有找到这个任务。", 404, "NOT_FOUND")
        request = json.loads(job["request"])
        code = job["error_code"]
        return {
            "id": job["id"], "name": "seal_" + job["id"],
            "status": "generating" if job["status"] == "creating" else job["status"],
            "createdAt": datetime.fromtimestamp(job["created"], timezone.utc).isoformat(),
            "updatedAt": datetime.fromtimestamp(job["updated"], timezone.utc).isoformat(),
            "elapsedSeconds": max(0, int((job["updated"] if job["status"] in TERMINAL else time.time()) - job["created"])),
            "estimatedSeconds": 240 if request["kind"] == "text" else 60,
            "queuePosition": self.store.queue_position(job), "quoteCredits": job["quote"],
            "seal": json.loads(job["result"]) if job["result"] else None,
            "error": {"code": code, "message": ERRORS[code]} if code else None,
        }

    def get(self, job_id):
        return self.public_job(self.store.get(job_id))

    def publish_manifest(self):
        with self.manifest_lock:
            write_seals(self.public_dir / "generated.json", self.store.results())

    def fail(self, job_id, code):
        self.store.update(job_id, status="failed", error_code=code)
        LOG.warning("seal task %s failed: %s", job_id, code)

    def worker(self):
        while not self.stop.is_set():
            try:
                job_id = self.queue.get(timeout=0.1)
            except queue.Empty:
                continue
            retry = False
            try:
                retry = self.process(job_id)
            except Exception:
                # Never echo provider exceptions: they may contain keys or signed URLs.
                job = self.store.get(job_id)
                if job["status"] == "creating":
                    self.fail(job_id, "CREATE_UNCERTAIN")
                elif job["status"] != "completed":
                    self.fail(job_id, "PROCESSING_FAILED" if job["remote_id"] else "UPSTREAM_ERROR")
            finally:
                with self.enqueue_lock:
                    self.enqueued.discard(job_id)
                self.queue.task_done()
            if retry and not self.stop.wait(self.poll_interval):
                self.enqueue(job_id)

    def process(self, job_id):
        job = self.store.get(job_id)
        if job["status"] in TERMINAL:
            return False
        request = json.loads(job["request"])
        if not job["remote_id"]:
            self.store.update(job_id, status="quoting")
            payload = {"version": "G1-Turbo", "faceCount": 10000, "outputFormat": ["glb"], "enablePbr": True}
            if request["kind"] == "image":
                payload["img"] = self.client.upload(self.state_dir / "uploads" / request["image_file"])
            else:
                payload.update(prompt=request["prompt"], style="photorealistic")
            balance = self.client.balance()
            quote = self.client.quote(request["kind"], payload, balance["uniqueId"])
            cost = quote["estimatedCreditsTotal"]
            if isinstance(cost, bool) or not isinstance(cost, (int, float)) or not math.isfinite(cost) or cost < 0:
                raise ValueError("Invalid provider quote")
            self.store.update(job_id, quote=cost)
            if cost > self.limits[request["kind"]]:
                self.fail(job_id, "QUOTE_LIMIT")
                return False
            if not self.store.reserve(job_id, cost, self.daily_budget):
                self.fail(job_id, "DAILY_BUDGET")
                return False
            if self.stop.is_set():
                self.store.update(job_id, status="queued", reserved=0)
                return False
            try:
                remote_id = self.client.create(request["kind"], payload, quote["quoteId"])
            except lux3d.Lux3DError as exc:
                if "GENERATION_CONCURRENCY_LIMIT_EXCEEDED" in str(exc):
                    self.store.update(job_id, status="queued", reserved=0)
                    return True
                # A rejected or disconnected create is never automatically repeated.
                raise
            if not remote_id or str(remote_id).lower() in {"none", "null"}:
                raise ValueError("No task id returned")
            self.store.update(job_id, remote_id=str(remote_id), status="generating")
            job = self.store.get(job_id)

        while not self.stop.is_set():
            try:
                task = self.client.get_task(job["remote_id"])
            except Exception:
                LOG.warning("seal task %s: polling unavailable; retrying existing task", job_id)
                self.stop.wait(self.poll_interval)
                continue
            status = task.get("status")
            if status in lux3d.FAILED:
                self.fail(job_id, "GENERATION_FAILED")
                return False
            if status in lux3d.SUCCEEDED:
                self.store.update(job_id, status="downloading")
                self.finish(job_id, task, request)
                return False
            self.stop.wait(self.poll_interval)
        return False

    def finish(self, job_id, task, request):
        name = "seal_" + job_id
        folder = self.public_dir / "objects" / name
        folder.mkdir(parents=True, exist_ok=True)
        dest = folder / "model.glb"
        if not dest.exists():
            urls = self.client.result_urls(task)
            url = next((u for u in urls if isinstance(u, str) and urlsplit(u).scheme == "https"
                        and urlsplit(u).path.lower().endswith(".glb")), None)
            if not url:
                raise ValueError("No GLB output")
            part = folder / "model.glb.part"
            try:
                self.client.download(url, part, max_bytes=128 * 1024 * 1024)
                with part.open("rb") as f:
                    header = f.read(12)
                if len(header) != 12 or header[:4] != b"glTF" or int.from_bytes(header[4:8], "little") != 2 or int.from_bytes(header[8:12], "little") != part.stat().st_size:
                    raise ValueError("Invalid GLB output")
                os.replace(part, dest)
            finally:
                part.unlink(missing_ok=True)
        meta = self.make_metadata(dest, request["label"])
        seal = {"name": name, "label": request["label"], "cells": meta["cells"],
                "why": request["why"], "real": request["real"],
                "glb": f"objects/{name}/model.glb", "yaw": meta["yaw"]}
        self.store.update(job_id, status="completed", result=json.dumps(seal, ensure_ascii=False), error_code=None)
        self.publish_manifest()

    def retry_processing(self, job_id):
        """Operator-only repair: reuse the original remote task, never create another."""
        job = self.store.get(job_id)
        if not job or not job["remote_id"] or job["error_code"] != "PROCESSING_FAILED":
            raise RequestError("Only processing failures with a saved upstream task can be retried")
        self.store.update(job_id, status="downloading", error_code=None)
        self.enqueue(job_id)

    def close(self):
        self.stop.set()
        for t in self.threads:
            t.join()
        self.lock.close()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()
