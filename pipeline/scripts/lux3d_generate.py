"""Generate one Lux3D asset: balance -> quote -> create -> poll -> download.

Task ids are written to <out>/task.json right after creation, so an interrupted
run resumes with --resume instead of paying for a second create.

  python scripts/lux3d_generate.py --prompt "一只白色瓷碗" --out out/lux3d/bowl
  python scripts/lux3d_generate.py --out out/lux3d/bowl --resume
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys import lux3d  # noqa: E402


def kind_of(task_file):
    """Task kind recorded at creation (older task files predate the field)."""
    return json.loads(task_file.read_text()).get("kind", "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--prompt")
    ap.add_argument("--image", help="local image: image-to-3D instead of text-to-3D")
    ap.add_argument("--repaint", help="local GLB to repaint (material transfer); needs --material-img")
    ap.add_argument("--material-img", help="local material reference photo for --repaint")
    ap.add_argument("--four-view", help="local image: generate four views (front34, opposite34, side, back)")
    ap.add_argument("--images", nargs="+", help="several local images or URLs: multi-view image-to-3D (first = most informative)")
    ap.add_argument("--version", default="G1-Turbo", choices=["G1-Turbo", "G1"])
    ap.add_argument("--style", default="photorealistic")
    ap.add_argument("--faces", type=int, default=10000)
    ap.add_argument("--max-credits", type=int, default=30, help="refuse quotes above this")
    ap.add_argument("--out", required=True)
    ap.add_argument("--resume", action="store_true")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    task_file = out / "task.json"

    if args.resume:
        task_id = json.loads(task_file.read_text())["taskId"]
    else:
        if task_file.exists():
            sys.exit(f"{task_file} exists; use --resume or a new --out")
        kind = ("repaint" if args.repaint else "fourview" if args.four_view
                else "image" if (args.image or args.images) else "text")
        if kind == "fourview":
            payload = {"img": lux3d.upload(args.four_view)}
            print(f"uploaded {args.four_view}")
        elif kind == "image" and args.images:
            urls = [u if u.startswith("http") else lux3d.upload(u) for u in args.images]
            payload = {"imgs": urls, "version": args.version, "faceCount": args.faces, "outputFormat": ["glb"]}
            if args.version == "G1-Turbo":
                payload["enablePbr"] = True
            print(f"multi-view input: {len(urls)} images")
        elif kind == "repaint":
            payload = {"img": lux3d.upload(args.material_img), "meshUrl": lux3d.upload(args.repaint),
                       "version": "v3.0-standard", "outputFormat": ["glb"]}
            print(f"uploaded {args.material_img} and {args.repaint}")
        else:
            payload = {"version": args.version, "faceCount": args.faces, "outputFormat": ["glb"]}
            if kind == "image":
                payload["img"] = lux3d.upload(args.image)
                print(f"uploaded {args.image}")
            else:
                payload.update(prompt=args.prompt, style=args.style)
            if args.version == "G1-Turbo":
                payload["enablePbr"] = True  # G1 rejects this field
        bal = lux3d.balance()
        q = lux3d.quote(kind, payload, bal["uniqueId"])
        cost = q["estimatedCreditsTotal"]
        print(f"balance {bal['availableCredits']}, quote {cost} credits")
        if cost > args.max_credits:
            sys.exit(f"quote {cost} exceeds --max-credits {args.max_credits}")
        task_id = lux3d.create(kind, payload, q["quoteId"])
        task_file.write_text(json.dumps({"taskId": task_id, "kind": kind, "payload": payload, "quote": cost},
                                        ensure_ascii=False, indent=1))
        print(f"created task {task_id}")

    task = lux3d.wait(task_id)
    (out / "result.json").write_text(json.dumps(task, ensure_ascii=False, indent=1))
    seen = {}
    urls = lux3d.result_urls(task)
    if kind_of(task_file) == "fourview":
        (out / "views.json").write_text(json.dumps(dict(zip(lux3d.FOUR_VIEW_ORDER, urls)), indent=1))
    for url in urls:
        ext = Path(url.split("?")[0]).suffix or ".bin"
        seen[ext] = seen.get(ext, -1) + 1  # number per extension: the GLB is always output_0.glb
        path = lux3d.download(url, out / f"output_{seen[ext]}{ext}")
        print(f"downloaded {path} ({path.stat().st_size / 1e6:.1f} MB)")
    print(f"balance now {lux3d.balance()['availableCredits']}")


if __name__ == "__main__":
    main()
