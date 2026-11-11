"""Turn a GLB (Lux3D output) into a printable tactile model with a braille base.

  python scripts/make_tactile.py out/lux3d/bowl/output_0.glb --title 碗 \
      --line 实物口径约十五厘米 --size 100 --out out/tactile/bowl

Part numbers (a person confirms what the automatic split found):
  ... --parts-review                         # writes parts_review.png, nothing printed
  ... --parts "1-4=甬;5=枚;6=钟体"           # numbers on the parts + legend_card.stl
"""
import argparse
import sys
from pathlib import Path

import matplotlib
import numpy as np
import trimesh

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from mpl_toolkits.mplot3d.art3d import Poly3DCollection  # noqa: E402

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys import parts as seg  # noqa: E402
from wwys import tactile  # noqa: E402

Y_UP_TO_Z_UP = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])


def parse_parts(spec, n_found):
    """"1-4=甬;5=枚" -> [("甬", [0, 1, 2, 3]), ("枚", [4])] (0-based part indices)."""
    groups = []
    for item in filter(None, (s.strip() for s in spec.split(";"))):
        ids, name = item.split("=", 1)
        idx = []
        for tok in ids.split(","):
            a, _, b = tok.partition("-")
            idx += list(range(int(a), int(b or a) + 1))
        if max(idx) > n_found:
            sys.exit(f"part {max(idx)} requested but only {n_found} found; run --parts-review")
        groups.append((name.strip(), [i - 1 for i in idx]))
    return groups


def review(mesh, found, path):
    colours = plt.cm.tab10(np.arange(len(found)) % 10)[:, :3]
    fc = np.full((len(mesh.faces), 3), 0.8)
    for i, p in enumerate(found):
        fc[p["faces"]] = colours[i]
    fig = plt.figure(figsize=(11, 5.5), dpi=100)
    for k, (elev, azim) in enumerate([(15, -90), (15, 0)]):
        ax = fig.add_subplot(1, 2, k + 1, projection="3d")
        light = np.array([0.3, -0.8, 0.5]) / np.linalg.norm([0.3, -0.8, 0.5])
        shade = np.clip(mesh.face_normals @ light, 0, 1)[:, None] * 0.6 + 0.4
        ax.add_collection3d(Poly3DCollection(mesh.triangles, facecolors=fc * shade, linewidths=0))
        for i, p in enumerate(found):
            ax.text(*p["centre"], str(i + 1), fontsize=13, weight="bold", color="k")
        lo, hi = mesh.bounds
        c, r = (lo + hi) / 2, (hi - lo).max() / 2
        ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
        ax.view_init(elev, azim)
        ax.set_axis_off()
    legend = "  ".join(f"{i + 1}: {p['kind']}" + (f" x{p['count']}" if "count" in p else "") for i, p in enumerate(found))
    fig.suptitle(legend, fontsize=10)
    fig.savefig(path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("glb")
    ap.add_argument("--title", required=True)
    ap.add_argument("--line", action="append", default=[], help="extra braille line (repeatable)")
    ap.add_argument("--size", type=float, default=100, help="model max extent, mm")
    ap.add_argument("--wall", type=float, default=2.4, help="minimum printable wall, mm")
    ap.add_argument("--parts-review", action="store_true")
    ap.add_argument("--parts", help='e.g. "1-4=甬;5=枚;6=钟体" (numbers from --parts-review)')
    ap.add_argument("--out", required=True)
    args = ap.parse_args()

    mesh = trimesh.load(args.glb, force="mesh").apply_transform(Y_UP_TO_Z_UP)
    mesh.merge_vertices(merge_tex=True, merge_norm=True)  # keeps face order; parts index faces
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    found = seg.segment(mesh) if (args.parts_review or args.parts) else []
    if args.parts_review:
        review(mesh, found, out / "parts_review.png")
        print(f"{len(found)} parts proposed; see {out / 'parts_review.png'}")
        for i, p in enumerate(found):
            print(f"  {i + 1}: {p['kind']} ({len(p['faces'])} faces{', x' + str(p['count']) if 'count' in p else ''})")
        return

    numbered = []
    if args.parts:
        groups = parse_parts(args.parts, len(found))
        numbered = [(str(n + 1), np.concatenate([found[i]["faces"] for i in ids])) for n, (_, ids) in enumerate(groups)]
    combined, layout = tactile.tactile_print(mesh, [args.title, *args.line], args.size, args.wall, parts=numbered)
    combined.export(out / "tactile.stl")
    tactile.preview_svg(layout, out / "preview.svg")
    print(f"base {layout['width']:.0f} x {layout['depth']:.0f} mm, {len(layout['dots'])} braille dots on the base")
    for text, cells in zip([args.title, *args.line], layout["braille"]):
        print(f"  {text} -> {cells}")
    if args.parts:
        for (name, _), num in zip(groups, layout["numbers"]):
            warn = f"  ({num['missed']} dots missed the surface)" if num["missed"] else ""
            print(f"  part {num['label']} {name}: {num['cells']} on the model, {num['dots']} dots{warn}")
        card, lines = tactile.legend_card([f"{i + 1} {name}" for i, (name, _) in enumerate(groups)])
        card.export(out / "legend_card.stl")
        print(f"  legend card: {len(lines)} lines -> {out / 'legend_card.stl'}")
    print(f"wrote {out / 'tactile.stl'} ({len(combined.faces)} faces)")


if __name__ == "__main__":
    main()
