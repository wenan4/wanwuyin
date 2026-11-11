"""Material recognition with a visual review step.

  python scripts/material_review.py sheet out/lux3d/yongzhong [--source photo.jpg] [--text 编钟]
  python scripts/material_review.py set   out/lux3d/yongzhong bronze --note "整件青铜，带锈"

`sheet` runs the automatic rules and writes review.png (source photo, texture-coloured
model, region map). `set` records the reviewed label in material.json, which
build_demo.py --material auto prefers over the automatic guess. Until a VLM is
wired in, the reviewer is whoever looks at review.png (for now: Claude).
"""
import argparse
import json
import sys
from pathlib import Path

import matplotlib
import numpy as np
import trimesh

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys import material_id as mid  # noqa: E402
from wwys.materials import MATERIALS  # noqa: E402

Y_UP_TO_Z_UP = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
LABEL_COLOURS = {"porcelain": "#f2efe4", "glass": "#9fd6e8", "bronze": "#b5853e", "steel": "#8a96a3",
                 "aluminum": "#d6dbe0", "stone": "#7d7a73", "wood": "#a0612b", "plastic": "#3f86d9"}


def _draw(ax, mesh, colours, title):
    keep = np.random.default_rng(0).permutation(len(mesh.faces))[:40000]
    tri = mesh.vertices[mesh.faces[keep]]
    light = np.array([0.4, -0.7, 0.6]) / np.linalg.norm([0.4, -0.7, 0.6])
    shade = np.clip(mesh.face_normals[keep] @ light, 0, 1)[:, None] * 0.6 + 0.4
    ax.add_collection3d(Poly3DCollection(tri, facecolors=np.clip(colours[keep] * shade, 0, 1), linewidths=0))
    lo, hi = mesh.bounds
    c, r = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.view_init(18, -70)
    ax.set_axis_off()
    ax.set_title(title, fontsize=10)


def sheet(folder, source=None, text=None):
    folder = Path(folder)
    mesh = trimesh.load(folder / "output_0.glb", force="mesh", process=False)
    res = mid.recognise(mesh, text)
    view = mesh.copy().apply_transform(Y_UP_TO_Z_UP)
    tex = mid.face_features(mesh)["rgb"]
    lab = np.array([matplotlib.colors.to_rgb(LABEL_COLOURS[mid.LABELS[i]]) for i in res["face_labels"]])

    fig = plt.figure(figsize=(13, 4.6), dpi=100)
    if source:
        ax = fig.add_subplot(1, 3, 1)
        ax.imshow(plt.imread(source))
        ax.set_axis_off()
        ax.set_title("source", fontsize=10)
    _draw(fig.add_subplot(1, 3, 2, projection="3d"), view, tex, "Lux3D texture")
    summary = ", ".join(f"{r['material']} {r['area']:.0%}" for r in res["regions"])
    _draw(fig.add_subplot(1, 3, 3, projection="3d"), view, lab, f"auto: {summary}")
    plt.tight_layout()
    fig.savefig(folder / "review.png")
    auto = {"material": res["material"], "regions": res["regions"], "text": text}
    (folder / "material_auto.json").write_text(json.dumps(auto, ensure_ascii=False, indent=1))
    print(f"auto -> {res['material']} ({summary}); wrote {folder / 'review.png'}")


def set_label(folder, material, note):
    folder = Path(folder)
    if material not in MATERIALS:
        sys.exit(f"unknown material {material}; choose from {list(MATERIALS)}")
    auto_path = folder / "material_auto.json"
    auto = json.loads(auto_path.read_text()) if auto_path.exists() else None
    rec = {"material": material, "reviewer": "visual review", "note": note, "auto": auto}
    (folder / "material.json").write_text(json.dumps(rec, ensure_ascii=False, indent=1))
    agree = auto and auto["material"] == material
    print(f"{folder.name}: reviewed {material}" + (f" (auto said {auto['material']})" if auto and not agree else ""))


def resolve(folder, text=None):
    """Reviewed label if present, else the automatic one: (material, source)."""
    folder = Path(folder)
    if (folder / "material.json").exists():
        return json.loads((folder / "material.json").read_text())["material"], "reviewed"
    mesh = trimesh.load(folder / "output_0.glb", force="mesh", process=False)
    return mid.recognise(mesh, text)["material"], "auto"


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("sheet")
    s.add_argument("folder")
    s.add_argument("--source")
    s.add_argument("--text")
    t = sub.add_parser("set")
    t.add_argument("folder")
    t.add_argument("material")
    t.add_argument("--note", default="")
    args = ap.parse_args()
    if args.cmd == "sheet":
        sheet(args.folder, args.source, args.text)
    else:
        set_label(args.folder, args.material, args.note)


if __name__ == "__main__":
    main()
