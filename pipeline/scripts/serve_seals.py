"""Run the maker backend: python scripts/serve_seals.py --port 8000."""
import argparse
import ipaddress
import logging
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from wwys.seal_api import make_server  # noqa: E402
from wwys.seal_jobs import SealService  # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="Lux3D seal generation API (YOU-197)")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8000)
    ap.add_argument("--state-dir", type=Path, default=ROOT / "out" / "seal-service")
    ap.add_argument("--public-dir", type=Path, default=ROOT.parent / "web" / "public")
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--daily-budget", type=float, default=100)
    ap.add_argument("--text-limit", type=float, default=15)
    ap.add_argument("--image-limit", type=float, default=3)
    ap.add_argument("--poll-interval", type=float, default=5)
    ap.add_argument("--origin", action="append", default=None, help="allowed browser origin; repeatable")
    ap.add_argument("--retry-processing", metavar="JOB_ID", help="repair metadata/download using the saved upstream task")
    args = ap.parse_args()
    token = os.environ.get("SEAL_API_TOKEN")
    try:
        loopback = ipaddress.ip_address(args.host).is_loopback
    except ValueError:
        loopback = args.host == "localhost"
    if not loopback and not token:
        ap.error("set SEAL_API_TOKEN before listening outside localhost")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    origins = args.origin if args.origin is not None else [
        "http://localhost:5199", "http://127.0.0.1:5199",
        "http://localhost:5173", "http://127.0.0.1:5173",
        f"http://localhost:{args.port}", f"http://127.0.0.1:{args.port}",
    ]
    with SealService(args.state_dir, args.public_dir, workers=args.workers,
                     daily_budget=args.daily_budget, text_limit=args.text_limit,
                     image_limit=args.image_limit, poll_interval=args.poll_interval) as service:
        if args.retry_processing:
            service.retry_processing(args.retry_processing)
        with make_server(service, args.host, args.port, token=token, origins=origins) as server:
            logging.info("Seal API listening on %s:%s", args.host, args.port)
            try:
                server.serve_forever(poll_interval=0.25)
            except KeyboardInterrupt:
                pass


if __name__ == "__main__":
    main()
