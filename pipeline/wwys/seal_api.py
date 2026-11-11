"""Small same-origin HTTP API for SealService, with bounded uploads."""
import hmac
import json
import logging
import re
from email import policy
from email.parser import BytesParser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from .seal_jobs import MAX_IMAGE_BYTES, RequestError

LOG = logging.getLogger(__name__)
MAX_BODY_BYTES = MAX_IMAGE_BYTES * 4 // 3 + 64 * 1024
JOB_PATH = re.compile(r"/api/seals/([a-f0-9]{32})")
MODEL_PATH = re.compile(r"/objects/(seal_[a-f0-9]{32})/model\.glb")


def parse_body(content_type, body):
    media_type = content_type.split(";", 1)[0].strip().lower()
    if media_type == "application/json":
        try:
            return json.loads(body)
        except (ValueError, UnicodeError):
            raise RequestError("请求体不是有效的 JSON。") from None
    if media_type == "multipart/form-data":
        message = BytesParser(policy=policy.default).parsebytes(
            b"Content-Type: " + content_type.encode("ascii") + b"\r\nMIME-Version: 1.0\r\n\r\n" + body)
        if not message.is_multipart() or message.defects:
            raise RequestError("无效的 multipart 表单。")
        data = {}
        for part in message.iter_parts():
            name = part.get_param("name", header="content-disposition")
            if name not in {"label", "why", "real", "prompt", "image"} or name in data:
                raise RequestError("表单字段无效或重复。")
            value = part.get_payload(decode=True)
            if not isinstance(value, bytes):
                raise RequestError("表单字段无效。")
            try:
                data[name] = value if name == "image" else value.decode("utf-8")
            except UnicodeError:
                raise RequestError("表单文字必须使用 UTF-8。") from None
        return data
    raise RequestError("请使用 application/json 或 multipart/form-data。", 415)


def make_server(service, host="127.0.0.1", port=8000, *, token=None, origins=()):
    allowed_origins = frozenset(origins)

    class Handler(BaseHTTPRequestHandler):
        timeout = 15

        def log_message(self, format, *args):
            # Do not put URL query strings, headers, bodies, or provider errors in logs.
            LOG.info("HTTP %s", self.command)

        def send_json(self, status, data):
            body = json.dumps(data, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            if token and not hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token):
                raise RequestError("需要有效的 Bearer token。", 401, "UNAUTHORIZED")

        def do_GET(self):
            try:
                self.authorized()
                path = urlsplit(self.path).path
                if path == "/api/health":
                    self.send_json(200, {"status": "ok"})
                elif path == "/api/seals":
                    self.send_json(200, {"seals": service.store.results()})
                elif match := JOB_PATH.fullmatch(path):
                    self.send_json(200, service.get(match[1]))
                elif path == "/generated.json":
                    self.send_json(200, service.store.results())
                elif match := MODEL_PATH.fullmatch(path):
                    model = service.public_dir / "objects" / match[1] / "model.glb"
                    job = service.store.get(match[1][5:])
                    if not job or job["status"] != "completed" or not model.is_file():
                        raise RequestError("模型不存在或尚未完成。", 404, "NOT_FOUND")
                    with model.open("rb") as f:
                        self.send_response(200)
                        self.send_header("Content-Type", "model/gltf-binary")
                        self.send_header("Content-Length", str(model.stat().st_size))
                        self.send_header("X-Content-Type-Options", "nosniff")
                        self.end_headers()
                        while chunk := f.read(64 * 1024):
                            self.wfile.write(chunk)
                else:
                    raise RequestError("接口不存在。", 404, "NOT_FOUND")
            except RequestError as exc:
                self.send_json(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "服务暂时不可用。"}})

        def do_POST(self):
            try:
                self.authorized()
                origin = self.headers.get("Origin")
                if origin and origin not in allowed_origins:
                    raise RequestError("不允许的请求来源。", 403, "ORIGIN_DENIED")
                if urlsplit(self.path).path != "/api/seals":
                    raise RequestError("接口不存在。", 404, "NOT_FOUND")
                try:
                    length = int(self.headers.get("Content-Length", ""))
                except ValueError:
                    raise RequestError("必须提供 Content-Length。", 411) from None
                if self.headers.get("Transfer-Encoding") or length <= 0:
                    raise RequestError("请求体长度无效。")
                if length > MAX_BODY_BYTES:
                    raise RequestError("请求体过大，照片最大 8 MiB。", 413)
                body = self.rfile.read(length)
                if len(body) != length:
                    raise RequestError("请求体不完整。")
                data = parse_body(self.headers.get("Content-Type", ""), body)
                job, created = service.submit(data, self.headers.get("Idempotency-Key"))
                self.send_json(202 if created else 200, job)
            except RequestError as exc:
                self.send_json(exc.status, {"error": {"code": exc.code, "message": str(exc)}})
            except (BrokenPipeError, ConnectionResetError):
                pass
            except Exception:
                self.send_json(500, {"error": {"code": "INTERNAL_ERROR", "message": "服务暂时不可用。"}})

    return ThreadingHTTPServer((host, port), Handler)
