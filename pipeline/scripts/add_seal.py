"""Generate -> download -> braille/yaw -> publish, using the same durable workers."""
import argparse
import base64
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wwys.seal_jobs import MAX_IMAGE_BYTES, SealService  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Generate and register one seal, or resume a saved job")
    source = ap.add_mutually_exclusive_group(required=True)
    source.add_argument("--prompt")
    source.add_argument("--image", type=Path)
    source.add_argument("--resume", metavar="JOB_ID")
    ap.add_argument("--label")
    ap.add_argument("--why")
    ap.add_argument("--real")
    ap.add_argument("--state-dir", type=Path, default=ROOT / "out" / "seal-service")
    ap.add_argument("--public-dir", type=Path, default=ROOT.parent / "web" / "public")
    ap.add_argument("--daily-budget", type=float, default=100)
    args = ap.parse_args()
    if not args.resume and not all((args.label, args.why, args.real)):
        ap.error("a new seal needs --label, --why and --real")
    data = {"label": args.label, "why": args.why, "real": args.real}
    if args.image:
        if args.image.stat().st_size > MAX_IMAGE_BYTES:
            ap.error("image must be at most 8 MiB")
        data["image"] = base64.b64encode(args.image.read_bytes()).decode("ascii")
    elif args.prompt:
        data["prompt"] = args.prompt
    with SealService(args.state_dir, args.public_dir, workers=1, daily_budget=args.daily_budget) as service:
        if args.resume:
            job = service.get(args.resume)
            if job["error"] and job["error"]["code"] == "PROCESSING_FAILED":
                service.retry_processing(args.resume)
        else:
            job, _ = service.submit(data)
        last = None
        while True:
            job = service.get(job["id"])
            if job["status"] != last:
                print(json.dumps(job, ensure_ascii=False), flush=True)
                last = job["status"]
            if job["status"] in {"completed", "failed"}:
                return 0 if job["status"] == "completed" else 1
            time.sleep(0.5)


if __name__ == "__main__":
    sys.exit(main())
