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
import shutil
import sys
from pathlib import Path

import numpy as np
import trimesh

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys.tactile import to_braille  # noqa: E402

ROUND = 1.12  # plan aspect (long axis / short axis) below which a model counts as round
ORDER = ["name", "label", "cells", "why", "real", "glb", "yaw"]


def plan_yaw(mesh, samples=200_000, seed=0):
    """(yaw for three.js rotation.y in radians, plan aspect ratio)."""
    pts, _ = trimesh.sample.sample_surface(mesh, samples, seed=seed)
    xz = pts[:, [0, 2]] - pts[:, [0, 2]].mean(0)
    w, v = np.linalg.eigh(np.cov(xz.T))
    aspect = float(np.sqrt(w[1] / w[0]))
    phi = float(np.arctan2(v[1, 1], v[0, 1]))  # long axis, measured from x towards z
    phi = (phi + np.pi / 2) % np.pi - np.pi / 2  # an axis has no direction: fold to [-90°, 90°)
    return (0.0 if aspect < ROUND else round(phi, 3)), aspect


def write_seals(path, seals):
    """One seal per line, the layout the file has always had."""
    body = ",\n".join("  " + json.dumps(s, ensure_ascii=False) for s in seals)
    Path(path).write_text(f"[\n{body}\n]\n", encoding="utf-8")


def upsert(path, name, glb, meta, label=None, why=None, real=None):
    seals = json.loads(Path(path).read_text(encoding="utf-8"))
    i = next((k for k, s in enumerate(seals) if s["name"] == name), None)
    if i is None:
        missing = [f"--{k}" for k, v in (("label", label), ("why", why), ("real", real)) if not v]
        if missing:
            sys.exit(f"{name} is a new seal: also give {' '.join(missing)}")
        seals.append({"name": name})
        i = len(seals) - 1
    dest = Path(path).parent / "objects" / name / "model.glb"
    if Path(glb).resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(glb, dest)
        print(f"copied {glb} -> {dest}")
    entry = dict(seals[i], glb=f"objects/{name}/model.glb", yaw=meta["yaw"])
    entry.update({k: v for k, v in (("label", label), ("why", why), ("real", real),
                                    ("cells", meta.get("cells"))) if v})
    seals[i] = {k: entry[k] for k in ORDER if k in entry} | {k: v for k, v in entry.items() if k not in ORDER}
    write_seals(path, seals)
    print(f"wrote {name} to {path}")


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
