"""Procedural stand-ins for Lux3D output while no API key is wired up.

Lathe shapes are revolved (r, z) profiles that start and end on the axis, so the
result is a closed solid that voxelises like any generated GLB would.
"""
import numpy as np
import trimesh


def _revolve(profile, sections=96):
    return trimesh.creation.revolve(np.asarray(profile, float), sections=sections)


def bowl(diameter=0.14, height=0.065, wall=0.005, foot=0.022):
    R = diameter / 2
    th = np.linspace(0, np.pi / 2, 24)
    outer = [(foot + (R - foot) * np.sin(t), height * (1 - np.cos(t)) + 0.006) for t in th]
    inner = [(max(r - wall, 0.0), z + wall * 0.6) for r, z in outer[::-1]]
    rim = [(R - wall / 2, height + 0.006 + wall * 0.3)]
    prof = [(0, 0.004), (foot - 0.003, 0.004), (foot - 0.002, 0), (foot + 0.003, 0)]
    prof += outer + rim + inner + [(0, 0.006 + wall * 0.6)]
    return _revolve(prof)


def bell(height=0.20, lip_radius=0.09):
    z = np.linspace(0, height, 30)
    flare = (1 - z / height) ** 2.2
    r_out = lip_radius * (0.52 + 0.48 * flare)
    thick = 0.007 + 0.009 * np.exp(-z / 0.02)  # heavy sound-bow at the lip
    r_in = r_out - thick
    crown, knob_r, knob_h = 0.012, 0.012, 0.03
    prof = [(0, height + knob_h), (knob_r, height + knob_h), (knob_r, height)]
    prof += list(zip(r_out[::-1], z[::-1]))
    prof += list(zip(r_in, z))[:-1] + [(r_in[-1] * 0.6, height - crown), (0, height - crown)]
    return _revolve(prof)


def cup(diameter=0.08, height=0.10, wall=0.003, base=0.006):
    R = diameter / 2
    prof = [(0, 0), (R, 0), (R, height), (R - wall, height), (R - wall, base), (0, base)]
    return _revolve(prof)


def bar(length=0.25, width=0.032, thickness=0.009):
    return trimesh.creation.box(extents=(length, width, thickness))


def lens(x, y, a, b):
    """Inside a lens of half-width a (x) and half-depth b (y): two arcs meeting at x = +-a.

    This is the 合瓦形 section of Chinese chimes; b == a degenerates to a circle.
    """
    b = np.minimum(b, a)
    R = (a * a + b * b) / (2 * b)
    c = R - b
    return (x * x + (y - c) ** 2 <= R * R) & (x * x + (y + c) ** 2 <= R * R)


def lens_bell_occupancy(ratio, h, height=0.30, a_top=0.085, a_mouth=0.115, wall=0.015, top=0.02):
    """Voxel occupancy of a flared chime with a lens section of depth/width `ratio`."""
    n = (np.array([2 * a_mouth, 2 * a_mouth, height]) / h).astype(int) + 4
    x = (np.arange(n[0]) - n[0] / 2 + 0.5) * h
    y = (np.arange(n[1]) - n[1] / 2 + 0.5) * h
    z = (np.arange(n[2]) - 1.5) * h
    X, Y, Z = np.meshgrid(x, y, z, indexing="ij")
    a = a_mouth + (a_top - a_mouth) * (Z / height)  # flares towards the mouth
    outer = lens(X, Y, a, ratio * a) & (Z >= 0) & (Z <= height)
    inner = lens(X, Y, a - wall, ratio * a - wall) & (Z < height - top)
    return (outer & ~inner).astype(float), x, y, z


def lens_contour(a, b, k=96):
    """Closed lens outline (k points, counter-clockwise) with sharp corners at x = +-a."""
    b = min(b, a)
    R = (a * a + b * b) / (2 * b)
    c = R - b
    phi0 = np.arcsin(np.clip(c / R, -1, 1))
    t = np.linspace(phi0, np.pi - phi0, k // 2, endpoint=False)
    upper = np.stack([R * np.cos(t), -c + R * np.sin(t)], axis=1)  # right corner -> left, over the top
    lower = upper * [-1, -1]                                     # left corner -> right, under the bottom
    return np.concatenate([upper, lower])


def lens_bell(ratio=0.65, height=0.30, a_top=0.085, a_mouth=0.115, wall=0.015, top=0.02, k=96, rings=60):
    """Smooth watertight chime with a lens (合瓦形) section, lofted analytically.

    Same proportions as lens_bell_occupancy. Marching cubes on a voxel grid left
    chevron-shaped steps on the slanted, flaring faces.
    """
    def ring(z, inset):
        a = a_mouth + (a_top - a_mouth) * z / height - inset
        xy = lens_contour(a, ratio * (a + inset) - inset, k)
        return np.column_stack([xy, np.full(len(xy), z)])

    zo = np.linspace(0, height, rings)
    zi = np.linspace(0, height - top, rings)
    outer = np.concatenate([ring(z, 0) for z in zo])
    inner = np.concatenate([ring(z, wall) for z in zi])
    verts = np.concatenate([outer, inner, [[0, 0, height], [0, 0, height - top]]])
    n_o, top_c, ceil_c = len(outer), len(verts) - 2, len(verts) - 1
    faces = []
    for j in range(rings - 1):
        for i in range(k):
            a0, a1 = j * k + i, j * k + (i + 1) % k
            b0, b1 = a0 + k, a1 + k
            faces += [[a0, a1, b1], [a0, b1, b0]]                                   # outer wall, outward
            faces += [[n_o + a0, n_o + b1, n_o + a1], [n_o + a0, n_o + b0, n_o + b1]]  # inner wall, inward
    for i in range(k):
        i1 = (i + 1) % k
        faces += [[i, n_o + i, n_o + i1], [i, n_o + i1, i1]]                      # mouth lip (faces down)
        t0, t1 = (rings - 1) * k + i, (rings - 1) * k + i1
        faces += [[t0, t1, top_c]]                                                # top plate (舞)
        faces += [[n_o + t1, n_o + t0, ceil_c]]                                   # cavity ceiling
    m = trimesh.Trimesh(verts, np.array(faces), process=True)
    m.fix_normals()
    return m


def plate(side=0.24, thickness=0.004):
    """Free square plate: the classic Chladni figure carrier."""
    return trimesh.creation.box(extents=(side, side, thickness))


DEMO = {
    # name: (builder, 中文名, default material, real max extent m, strike hint in normalised coords)
    "bowl": (bowl, "碗", "porcelain", 0.14, "rim"),
    "bell": (bell, "钟", "bronze", 0.23, "lip"),
    "cup": (cup, "杯", "glass", 0.10, "rim"),
    "bar": (bar, "金属条", "aluminum", 0.25, "end"),
    "plate": (plate, "方板", "aluminum", 0.24, "end"),
    # Same chime, three sections: 一钟双音 grows as the 合瓦形 section flattens.
    "bianzhong_r100": (lambda: lens_bell(1.0), "编钟·圆截面", "bronze", 0.30, [("正鼓", "zhenggu"), ("侧鼓", "cegu_lens")]),
    "bianzhong_r080": (lambda: lens_bell(0.8), "编钟·合瓦形 0.8", "bronze", 0.30, [("正鼓", "zhenggu"), ("侧鼓", "cegu_lens")]),
    "bianzhong_r065": (lambda: lens_bell(0.65), "编钟·合瓦形 0.65", "bronze", 0.30, [("正鼓", "zhenggu"), ("侧鼓", "cegu_lens")]),
}
# Finer display meshes where the pattern matters (Chladni sand needs dense top faces).
DISPLAY_EDGE = {"plate": 0.01}


# Chime strike spots as a fraction of the bell width from the front centre.
# 正鼓 is the centre; 侧鼓 sits on the 正鼓 mode's node line, which the scan in
# docs (Lux3D 甬钟, 2026-10-31) put at ~0.35 of the width.
BELL_FACE = {"zhenggu": 0.0, "cegu": 0.35, "cegu_lens": 0.25}  # cegu_lens: two_tone_bell.py study


def bell_face_vertex(mesh_norm, x_frac, height_frac=0.06):
    """Front-face vertex (z-up, front = min y) a little above the mouth edge."""
    v = mesh_norm.vertices
    lo, hi = mesh_norm.bounds
    front = v[:, 1] < lo[1] + 0.4 * (hi[1] - lo[1])
    xt = (lo[0] + hi[0]) / 2 + x_frac * (hi[0] - lo[0])
    near = front & (np.abs(v[:, 0] - xt) < 0.015)
    zt = v[near, 2].min() + height_frac * (hi[2] - lo[2])
    score = np.where(front, -4 * np.abs(v[:, 0] - xt) - 4 * np.abs(v[:, 2] - zt) + (lo[1] - v[:, 1]), -np.inf)
    return int(np.argmax(score))


def strike_vertex(mesh_norm, hint):
    """Pick a sensible default strike vertex on a normalised, z-up mesh."""
    v = mesh_norm.vertices
    if hint in BELL_FACE:
        return bell_face_vertex(mesh_norm, BELL_FACE[hint])
    c = v.mean(axis=0)
    r = np.hypot(v[:, 0] - c[0], v[:, 1] - c[1])
    if hint == "rim":
        score = v[:, 2] + 0.5 * r
    elif hint == "lip":
        score = -v[:, 2] + 0.5 * r
    else:  # "end": far end of a bar, on top
        score = v[:, 0] + 0.2 * v[:, 2]
    return int(np.argmax(score))
