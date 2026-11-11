"""Minimal Lux3D OpenAPI client (cn region): balance, quote, create, poll, download.

Endpoints and envelopes follow the official aholo-lux3d skill runtime. The API key
is read from pipeline/.env (LUX3D_CN_API_KEY) and is never logged.
"""
import hashlib
import json
import os
import time
import urllib.parse
import urllib.request
import uuid
from pathlib import Path

BASE = "https://api.aholo3d.cn"
SOURCE = 2  # host id registered for Claude Code
CREATE_PATHS = {
    "text": "/lux3d/v1/generate/text-to-3d/task/create",
    "image": "/lux3d/v1/generate/img-to-3d/task/create",
    "repaint": "/lux3d/v1/generate/material-transfer/task/create",  # img + meshUrl (.glb)
    # Output is one JSON-array string: front-three-quarter, opposite-three-quarter, side, back.
    "fourview": "/lux3d/v1/generate/image-to-four-view/task/create",
}
FOUR_VIEW_ORDER = ["front34", "opposite34", "side", "back"]
RUNNING, SUCCEEDED, FAILED = {0, 1, 2}, {3}, {4, 5, 6}


class Lux3DError(RuntimeError):
    pass


def _api_key():
    key = os.environ.get("LUX3D_CN_API_KEY")
    env = Path(__file__).resolve().parents[1] / ".env"
    if not key and env.exists():
        for line in env.read_text().splitlines():
            if line.startswith("LUX3D_CN_API_KEY="):
                key = line.split("=", 1)[1].strip()
    if not key:
        raise Lux3DError("LUX3D_CN_API_KEY missing (set it in pipeline/.env)")
    return key


def _call(method, path, query=None, body=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(query) if query else "")
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "Content-Type": "application/json", "Authorization": _api_key()})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            env = json.loads(r.read())
    except urllib.error.HTTPError as e:
        raise Lux3DError(f"HTTP {e.code} on {path}: {e.read()[:300]!r}") from None
    if "c" not in env:  # asset endpoints return the payload without the {f, c, m, d} envelope
        return env
    if env.get("c") != "0":
        raise Lux3DError(f"{path} rejected: c={env.get('c')} m={env.get('m')}")
    return env["d"]


def balance():
    return _call("GET", "/lux3d/v1/account/balance", {"source": SOURCE})


def quote(kind, payload, unique_id):
    item = {"endpoint": {"method": "POST", "path": CREATE_PATHS[kind]},
            "parameters": {"pathParameters": "{}", "queryParameters": "{}",
                           "body": json.dumps(payload, ensure_ascii=False)}}
    return _call("POST", "/lux3d/v1/pricing/openapi-quotes",
                 body={"source": SOURCE, "uniqueId": unique_id, "items": {"1": item}})


def create(kind, payload, quote_id=None):
    body = dict(payload, contentId=quote_id) if quote_id else payload
    return str(_call("POST", CREATE_PATHS[kind], body=body))


def get_task(task_id):
    return _call("GET", "/lux3d/v1/generate/task/get", {"taskid": task_id})


def wait(task_id, interval=15, max_wait=1200, log=print):
    t0 = time.time()
    while time.time() - t0 < max_wait:
        d = get_task(task_id)
        status = d.get("status")
        if status in SUCCEEDED:
            return d
        if status in FAILED:
            raise Lux3DError(f"task {task_id} ended with status {status}: {json.dumps(d, ensure_ascii=False)[:400]}")
        log(f"  task {task_id} status {status} ({time.time() - t0:.0f}s)")
        time.sleep(interval)
    raise Lux3DError(f"task {task_id} still running after {max_wait}s; resume with get_task")


def result_urls(task):
    """Non-empty output URLs (links expire after about two hours).

    Four-view tasks return a JSON array string in one slot; it is expanded here.
    """
    urls = []
    for o in task.get("outputs") or []:
        c = str(o.get("content") or "")
        if c.lower() in ("", "not_requested", "null", "none"):
            continue
        urls.extend(json.loads(c) if c.lstrip().startswith("[") else [c])
    return urls


def _multipart(fields, file_field, file_path):
    boundary = f"----wwys{uuid.uuid4().hex}"
    parts = []
    for k, v in fields.items():
        parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{k}"\r\n\r\n{v}\r\n'.encode())
    p = Path(file_path)
    parts.append(f'--{boundary}\r\nContent-Disposition: form-data; name="{file_field}"; filename="{p.name}"\r\n'
                 f"Content-Type: application/octet-stream\r\n\r\n".encode() + p.read_bytes() + b"\r\n")
    parts.append(f"--{boundary}--\r\n".encode())
    return b"".join(parts), f"multipart/form-data; boundary={boundary}"


def _ous(method, url, token, body=None, content_type=None):
    headers = {"ous-token-v2": token}
    if content_type:
        headers["Content-Type"] = content_type
    req = urllib.request.Request(url, data=body, method=method, headers=headers)
    with urllib.request.urlopen(req, timeout=120) as r:
        env = json.loads(r.read())
    if env.get("c") not in (None, "", "0", 0):
        raise Lux3DError(f"OUS {url.rsplit('/', 3)[-3:]} rejected: c={env.get('c')} m={env.get('m')}")
    return env["d"] if isinstance(env.get("d"), dict) else env


def _lack_blocks(spec, total):
    """Parse OUS lackBlocks ("" = all, "1,3-5" or a list), 1-indexed."""
    if spec in (None, "", []):
        return list(range(1, total + 1))
    items = spec if isinstance(spec, list) else str(spec).split(",")
    out = []
    for it in items:
        it = str(it).strip()
        if "-" in it:
            a, b = map(int, it.split("-"))
            out.extend(range(a, b + 1))
        elif it:
            out.append(int(it))
    return [b for b in out if 1 <= b <= total]


def upload(path, poll=120, interval=1.0):
    """Upload a local file via OUS and return its service URL.

    Flow from the official asset_uploader: gateway token -> single upload (or block
    init + missing parts above blockSize) on the token's globalDomain -> poll status
    until 5 (ok) / 6, 8 (failed). There is no separate commit call.
    """
    path = Path(path)
    tok = _call("GET", "/asset/v1/token")
    domain, token, block = tok["globalDomain"].rstrip("/"), tok["ousToken"].strip(), int(tok["blockSize"])
    data = path.read_bytes()
    md5 = hashlib.md5(data).hexdigest()
    if len(data) <= block:
        body, ctype = _multipart({"md5": md5}, "file", path)
        _ous("POST", f"{domain}/ous/api/v2/single/upload", token, body, ctype)
    else:
        total = -(-len(data) // block)
        form = urllib.parse.urlencode({"md5": md5, "blocks": total, "size": len(data), "name": path.name}).encode()
        init = _ous("POST", f"{domain}/ous/api/v2/block/upload/init", token, form, "application/x-www-form-urlencoded")
        if not init.get("deduplicated"):
            for b in _lack_blocks(init.get("lackBlocks"), total):
                part = Path(f"/tmp/{path.name}.part-{b}")  # multipart needs a filename; content is the slice
                part.write_bytes(data[(b - 1) * block: b * block])
                try:
                    body, ctype = _multipart({"block": b}, "file", part)
                    _ous("POST", f"{domain}/ous/api/v2/block/upload/part", token, body, ctype)
                finally:
                    part.unlink(missing_ok=True)
    for _ in range(poll):
        st = _ous("GET", f"{domain}/ous/api/v2/upload/status", token)
        if st.get("status") == 5:
            return st["url"]
        if st.get("status") in (6, 8):
            raise Lux3DError(f"upload failed: {st}")
        time.sleep(interval)
    raise Lux3DError("upload status polling timed out")


def download(url, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=300) as r, open(dest, "wb") as f:
        while chunk := r.read(1 << 20):
            f.write(chunk)
    return dest
