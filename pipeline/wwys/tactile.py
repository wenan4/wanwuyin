"""Tactile print: thickened, smoothed model on a base with embossed braille.

Units are millimetres, z-up. Braille comes from liblouis' zhcn-cbs table (国家通用盲文
方案); output still needs proofreading by a braille reader. Dot geometry defaults sit
in the common range for paper braille and must be test-printed with real readers.
"""
import shutil
import subprocess
from dataclasses import dataclass

import numpy as np
import trimesh
from skimage import measure

from . import fem

BRAILLE_TABLE = "zhcn-cbs.ctb"
# Unicode braille bit -> (column, row) inside a cell; dots 7/8 are unused here.
DOT_POS = {0: (0, 0), 1: (0, 1), 2: (0, 2), 3: (1, 0), 4: (1, 1), 5: (1, 2)}


@dataclass
class BrailleSpec:
    dot_diameter: float = 1.5
    dot_height: float = 0.6
    dot_pitch: float = 2.5   # within a cell
    cell_pitch: float = 6.0  # cell to cell
    line_pitch: float = 10.0


def to_braille(text, table=BRAILLE_TABLE):
    if not shutil.which("lou_translate"):
        raise RuntimeError("lou_translate not found (brew install liblouis)")
    out = subprocess.run(["lou_translate", "--forward", f"unicode.dis,{table}"],
                         input=text, capture_output=True, text=True, encoding="utf-8", check=True, timeout=30)
    return out.stdout.strip("\n")


def wrap_cells(braille, max_cells):
    """Wrap on braille spaces (U+2800) where possible."""
    lines, cur = [], ""
    for word in braille.split("⠀"):
        cand = f"{cur}⠀{word}" if cur else word
        if len(cand) <= max_cells or not cur:
            cur = cand
        else:
            lines.append(cur)
            cur = word
    lines.append(cur)
    return [ln[i:i + max_cells] for ln in lines for i in range(0, max(len(ln), 1), max_cells)]


def dot_centres(lines, spec, x0, y_top):
    """(x, y) of raised dots; rows run away from the reader (+y), so row 0 is farthest."""
    pts = []
    for li, line in enumerate(lines):
        for ci, ch in enumerate(line):
            bits = ord(ch) - 0x2800
            for b, (col, row) in DOT_POS.items():
                if bits >> b & 1:
                    pts.append((x0 + ci * spec.cell_pitch + col * spec.dot_pitch,
                                y_top - li * spec.line_pitch - row * spec.dot_pitch))
    return np.array(pts).reshape(-1, 2)


def domes(points, z_top, spec):
    """Spherical caps of the given base diameter and height, sunk into the plate."""
    r, h = spec.dot_diameter / 2, spec.dot_height
    R = (r * r + h * h) / (2 * h)
    proto = trimesh.creation.icosphere(subdivisions=2, radius=R)
    return [proto.copy().apply_translation([x, y, z_top + h - R]) for x, y in points]


def base_plate(width, depth, height, notch):
    """Rectangular plate with the front-left corner cut off as an orientation cue."""
    xy = [(notch, 0), (width, 0), (width, depth), (0, depth), (0, notch)]
    pts = [(x, y, z) for x, y in xy for z in (0, height)]
    return trimesh.convex.convex_hull(np.array(pts, float))


def printable(mesh, size_mm, wall_mm=2.4, pitch_mm=0.4, smooth=10, max_faces=150_000):
    """Scale to size, thicken to a minimum wall, rebuild as a smooth watertight solid.

    Thickening also makes thin parts (rims, handles, legs) strong enough to touch.
    Marching cubes at 0.4 mm gives ~500k faces; decimation keeps STLs slicer-friendly.
    """
    m = fem.normalize_mesh(mesh)
    n = int(round(size_mm / pitch_mm))
    occ, origin = fem.shell_voxelize(m, n, wall_mm / size_mm)
    verts, faces, _, _ = measure.marching_cubes(np.pad(occ, 1), level=0.5)
    verts = (verts - 1) / n + origin  # back to normalised units
    solid = trimesh.Trimesh(verts * size_mm, faces[:, ::-1], process=True)
    trimesh.smoothing.filter_taubin(solid, iterations=smooth)
    if len(solid.faces) > max_faces:
        solid = solid.simplify_quadric_decimation(face_count=max_faces)
    solid.fix_normals()
    return solid


def braille_on_surface(target, point, normal, cells, spec=BrailleSpec(), up=(0, 0, 1)):
    """Domes spelling `cells` centred at `point`, projected onto `target` along -normal.

    The reader faces the surface (looking along -normal) with `up` up, so text runs
    along cross(up, normal). Dots whose ray misses the part are dropped and counted.
    """
    n = np.asarray(normal, float) / np.linalg.norm(normal)
    u = np.asarray(up, float) - np.dot(up, n) * n
    if np.linalg.norm(u) < 1e-3:  # label on a top face: "up" is away from the reader
        u = np.array([0.0, 1.0, 0.0]) - n[1] * n
    u /= np.linalg.norm(u)
    right = np.cross(u, n)
    width = (len(cells) - 1) * spec.cell_pitch + spec.dot_pitch
    local = []
    for ci, ch in enumerate(cells):
        bits = ord(ch) - 0x2800
        for b, (col, row) in DOT_POS.items():
            if bits >> b & 1:
                local.append((ci * spec.cell_pitch + col * spec.dot_pitch - width / 2, spec.dot_pitch * (1 - row)))
    origins = np.array([point + x * right + y * u + n * 15 for x, y in local])
    locs, ray_i, _ = target.ray.intersects_location(origins, np.tile(-n, (len(origins), 1)), multiple_hits=False)
    r, h = spec.dot_diameter / 2, spec.dot_height
    R = (r * r + h * h) / (2 * h)
    proto = trimesh.creation.icosphere(subdivisions=2, radius=R)
    domes = [proto.copy().apply_translation(p + n * (h - R)) for p in locs]
    return domes, len(origins) - len(locs)


def legend_card(entries, spec=BrailleSpec(), margin=8.0, thick=2.5, max_cells=18):
    """Separate flat card: one braille line per "number name" entry."""
    lines = [ln for e in entries for ln in wrap_cells(to_braille(e), max_cells)]
    width = max(len(ln) for ln in lines) * spec.cell_pitch + 2 * margin
    depth = len(lines) * spec.line_pitch + 2 * margin
    card = base_plate(width, depth, thick, notch=6.0)
    pts = dot_centres(lines, spec, x0=margin + spec.dot_pitch, y_top=depth - margin - spec.line_pitch / 2 + spec.dot_pitch)
    return trimesh.util.concatenate([card, *domes(pts, thick, spec)]), lines


def tactile_print(mesh, lines_text, size_mm=100, wall_mm=2.4, spec=BrailleSpec(),
                  margin=8.0, base_h=4.0, sink=0.8, max_cells=18, parts=()):
    """parts: [(number label, face indices into `mesh`)] get braille numbers on the model."""
    model = printable(mesh, size_mm, wall_mm)
    sites = []
    if parts:
        from . import parts as seg
        ref = fem.normalize_mesh(mesh)
        ref.apply_scale(size_mm)  # same frame printable() builds the solid in
        # Place in order, keeping each new number clear of the ones already placed.
        for label, faces in parts:
            point, normal = seg.label_site(ref, np.asarray(faces), avoid=[p for _, p, _ in sites])
            sites.append((label, point, normal))
    braille_lines = [ln for t in lines_text for ln in wrap_cells(to_braille(t), max_cells)]
    text_w = max(len(ln) for ln in braille_lines) * spec.cell_pitch
    strip = len(braille_lines) * spec.line_pitch + 2
    fx, fy = model.extents[:2]
    width = max(fx, text_w) + 2 * margin
    depth = strip + fy + 3 * margin
    plate = base_plate(width, depth, base_h, notch=8.0)

    lo = model.bounds[0]
    shift = np.array([width / 2 - fx / 2 - lo[0], strip + 2 * margin - lo[1], base_h - sink - lo[2]])
    model.apply_translation(shift)
    pts = dot_centres(braille_lines, spec, x0=margin + spec.dot_pitch, y_top=margin + strip - spec.line_pitch / 2)
    pieces = [plate, model, *domes(pts, base_h, spec)]
    numbers = []
    for label, point, normal in sites:
        cells = to_braille(label)
        ds, missed = braille_on_surface(model, point + shift, normal, cells, spec)
        pieces += ds
        numbers.append({"label": label, "cells": cells, "dots": len(ds), "missed": missed})
    layout = {"width": width, "depth": depth, "braille": braille_lines, "dots": pts,
              "footprint": model.bounds[:, :2], "text": lines_text, "numbers": numbers}
    return trimesh.util.concatenate(pieces), layout


def preview_svg(layout, path, scale=4.0):
    """Top view: plate outline, model footprint, braille dots (reader at the bottom)."""
    W, D = layout["width"], layout["depth"]
    fy = lambda y: (D - y) * scale  # noqa: E731  svg y grows downward
    (x0, y0), (x1, y1) = layout["footprint"]
    notch = 8.0
    plate = f"{notch * scale},{fy(0)} {W * scale},{fy(0)} {W * scale},{fy(D)} 0,{fy(D)} 0,{fy(notch)}"
    dots = "".join(f'<circle cx="{x * scale:.1f}" cy="{fy(y):.1f}" r="{0.75 * scale:.1f}"/>' for x, y in layout["dots"])
    label = " / ".join(layout["text"])
    svg = f"""<svg xmlns="http://www.w3.org/2000/svg" width="{W * scale}" height="{D * scale + 40}" font-family="sans-serif">
<rect width="100%" height="100%" fill="#f6f4ef"/>
<polygon points="{plate}" fill="#e2ddd2" stroke="#6b665c"/>
<rect x="{x0 * scale}" y="{fy(y1)}" width="{(x1 - x0) * scale}" height="{(y1 - y0) * scale}" fill="none" stroke="#a0703f" stroke-dasharray="6 4"/>
<g fill="#222">{dots}</g>
<text x="8" y="{D * scale + 26}" font-size="14" fill="#333">{label}  ·  {W:.0f}×{D:.0f} mm  ·  读者在下方</text>
</svg>"""
    with open(path, "w", encoding="utf-8") as f:
        f.write(svg)
