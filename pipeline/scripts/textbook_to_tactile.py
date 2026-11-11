"""Labelled textbook figure -> numbered tactile print + braille legend.

  python scripts/textbook_to_tactile.py out/inputs/textbook/yongzhong_figure.png \
      --glb out/lux3d/textbook_yongzhong/output_0.glb --size 120 --out out/tactile/textbook_yongzhong

Steps: parse the figure (caption, labels, leader targets; clean crop for Lux3D),
register the model's front silhouette to the crop, cast each target onto the
surface, turn labels into face regions, then print with numbers where the
leaders point. Run without --glb to just write the clean crop for Lux3D.
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

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys import align, fem, figure, tactile  # noqa: E402
from wwys import parts as seg  # noqa: E402

Y_UP_TO_Z_UP = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])


def review(out, crop, mask, mesh, reg, hits, regions):
    fig, axes = plt.subplots(1, 2, figsize=(12, 7), dpi=100)
    ax = axes[0]
    ax.imshow(crop)
    sil = align.silhouette(mesh, (crop.height, crop.width), reg["s"], reg["ox"], reg["oy"], reg["mirror"], reg.get("sz"))
    ax.contour(sil, levels=[0.5], colors="magenta", linewidths=1)
    for name, uv, ok in hits:
        ax.plot(*uv, "o", color="yellow" if ok else "red", ms=8, mec="k")
        ax.annotate(name, uv, xytext=(8, -8), textcoords="offset points", color="yellow",
                    fontsize=14, fontname="Heiti TC" if "Heiti TC" in matplotlib.font_manager.get_font_names() else None)
    ax.set_title(f"magenta: registered model outline ({reg['method']} {reg['iou']:.2f})", fontsize=10)
    ax.set_axis_off()
    ax = axes[1]
    ax.remove()
    ax = fig.add_subplot(1, 2, 2, projection="3d")
    colours = plt.cm.tab10(np.arange(len(regions)) % 10)[:, :3]
    fc = np.full((len(mesh.faces), 3), 0.82)
    for i, (_, faces) in enumerate(regions):
        fc[faces] = colours[i]
    light = np.array([0.3, -0.8, 0.5]) / np.linalg.norm([0.3, -0.8, 0.5])
    shade = np.clip(mesh.face_normals @ light, 0, 1)[:, None] * 0.6 + 0.4
    from mpl_toolkits.mplot3d.art3d import Poly3DCollection
    ax.add_collection3d(Poly3DCollection(mesh.triangles, facecolors=fc * shade, linewidths=0))
    for i, (_, faces) in enumerate(regions):
        if len(faces):
            ax.text(*mesh.triangles_center[faces].mean(0), str(i + 1), fontsize=14, weight="bold")
    lo, hi = mesh.bounds
    c, r = (lo + hi) / 2, (hi - lo).max() / 2
    ax.set_xlim(c[0] - r, c[0] + r); ax.set_ylim(c[1] - r, c[1] + r); ax.set_zlim(c[2] - r, c[2] + r)
    ax.view_init(10, -90)
    ax.set_axis_off()
    ax.set_title("regions: " + "  ".join(f"{i + 1}" for i in range(len(regions))), fontsize=10)
    fig.tight_layout()
    fig.savefig(out / "textbook_review.png")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("figure")
    ap.add_argument("--glb", help="Lux3D model generated from the clean crop")
    ap.add_argument("--size", type=float, default=120, help="print max extent, mm")
    ap.add_argument("--line", action="append", default=[], help="extra braille line on the base")
    ap.add_argument("--repeated", nargs="*", default=[], help="labels that name a repeated detail (e.g. 枚)")
    ap.add_argument("--out", required=True)
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)

    r = figure.parse(args.figure)
    r["clean"].save(out / "clean.png")
    found_labels = [(lab["text"], lab["target"]) for lab in r["labels"]]
    print(f"caption -> title {r['title']!r}; labels {[t for t, _ in found_labels]}; leaders {r['lines']}")
    if not args.glb:
        print(f"wrote {out / 'clean.png'}: generate a model from it, then rerun with --glb")
        return

    mesh = trimesh.load(args.glb, force="mesh").apply_transform(Y_UP_TO_Z_UP)
    mesh.merge_vertices(merge_tex=True, merge_norm=True)
    ref = fem.normalize_mesh(mesh)
    ref.apply_scale(args.size)  # the frame tactile_print works in (mm)
    from wwys import material_id
    colours = material_id.face_features(ref)["rgb"]
    border = np.asarray(r["clean"].convert("L"), float)
    white_ground = np.mean(np.r_[border[:8].ravel(), border[-8:].ravel(), border[:, :8].ravel(), border[:, -8:].ravel()] > 235) > 0.8
    if white_ground:  # the usual textbook case: the object mask is trustworthy
        reg = align.register(ref, align.white_ground_mask(r["clean"]))
    else:  # photo backdrops defeat segmentation; flag for a teacher to confirm
        reg = align.register_texture(ref, colours, r["clean"])
        print("  dark/graded backdrop: automatic registration is unreliable here, check textbook_review.png")
    # Turn the model so its figure-facing side is the reader's side (-y) for casting,
    # numbering and printing alike.
    yaw = trimesh.transformations.rotation_matrix(np.deg2rad(reg["yaw"]), [0, 0, 1])
    ref.apply_transform(yaw)
    mesh.apply_transform(yaw)
    print(f"registration {reg['method']} {reg['iou']:.2f}, yaw {reg['yaw']}°{' (mirrored)' if reg['mirror'] else ''}")

    x0, y0 = r["crop"][:2]
    hits, shown = [], []
    for text, target in found_labels:
        if target is None:
            print(f"  {text}: no leader found, skipped")
            continue
        uv = (target[0] - x0, target[1] - y0)
        h = align.cast(ref, reg, uv, search=15.0)
        shown.append((text, uv, h is not None))
        if h is None:
            print(f"  {text}: leader end misses the model, skipped")
            continue
        hits.append((text, h[0], h[1]))
    found = seg.segment(ref)
    regions = seg.label_regions(ref, found, hits, repeated_labels=args.repeated)
    review(out, r["clean"], None, ref, reg, shown, regions)

    numbered = []
    for i, ((text, _, point), (_, faces)) in enumerate(zip(hits, regions)):
        # Put the number where the leader pointed: faces of the region near the hit.
        near = faces[np.linalg.norm(ref.triangles_center[faces] - point, axis=1) < 18.0]
        numbered.append((str(i + 1), near if len(near) >= 8 else faces))
        print(f"  {i + 1} {text}: {len(faces)} faces")
    title = r["title"] or "模型"
    combined, layout = tactile.tactile_print(mesh, [title, *args.line], args.size, parts=numbered)
    combined.export(out / "tactile.stl")
    card, _ = tactile.legend_card([f"{i + 1} {t}" for i, (t, _, _) in enumerate(hits)])
    card.export(out / "legend_card.stl")
    (out / "labels.json").write_text(json.dumps(
        {"title": title, "labels": [t for t, _, _ in hits], "iou": reg["iou"]}, ensure_ascii=False, indent=1))
    missed = sum(n["missed"] for n in layout["numbers"])
    print(f"wrote tactile.stl ({len(combined.faces)} faces), legend_card.stl, textbook_review.png;"
          f" {len(layout['numbers'])} numbers on the model" + (f", {missed} braille dots missed" if missed else ""))


if __name__ == "__main__":
    main()
