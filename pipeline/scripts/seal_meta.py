"""Braille cells and side-view yaw for a seal model, as stored in web/public/seals.json.

  python scripts/seal_meta.py ../web/public/objects/trex/model.glb --label 霸王龙
  python scripts/seal_meta.py out/lux3d/trex_v2/output_0.glb --label 霸王龙 \
      --into ../web/public/seals.json --name trex --why 已消失 --real "身长约 12 米"

With --into the model is copied to objects/<name>/model.glb next to seals.json and the
entry is updated, or added (a new seal also needs --label, --why and --real).

yaw turns the long axis of the model's plan view onto x, so long things (animals) show
their side on the knob and in the relief. glTF is y-up, so the plan is the x-z plane, and
seal.ts applies yaw as rotation.y. Nearly round plans (bells, planets, cones) keep the
model's own front (yaw 0). Braille is 国家通用盲文 via liblouis (needs lou_translate).
"""
import argparse
import json
import sys
from pathlib import Path

import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys.tactile import to_braille  # noqa: E402
from wwys.seal_meta import plan_yaw, upsert, write_seals  # noqa: E402,F401


def main():
    ap = argparse.ArgumentParser(description="Braille cells and side-view yaw for a seal model")
    ap.add_argument("glb")
    ap.add_argument("--label", help="Chinese name, translated to braille")
    ap.add_argument("--into", help="seals.json to update; the model is copied next to it")
    ap.add_argument("--name", help="seal id: key in seals.json and folder under objects/")
    ap.add_argument("--why", help="why a child cannot touch it, e.g. 太大 (needed for a new seal)")
    ap.add_argument("--real", help="real size, e.g. 身长约 6 米 (needed for a new seal)")
    args = ap.parse_args()
    if args.into and not args.name:
        ap.error("--into needs --name")

    yaw, aspect = plan_yaw(trimesh.load(args.glb, force="mesh"))
    meta = {"yaw": yaw, "aspect": round(aspect, 2)}
    if args.label:
        meta["cells"] = to_braille(args.label)
    print(json.dumps(meta, ensure_ascii=False))
    if args.into:
        upsert(args.into, args.name, args.glb, meta, args.label, args.why, args.real)


if __name__ == "__main__":
    main()
