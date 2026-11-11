"""Register a Lux3D model's front view with the figure it came from.

Image-to-3D keeps the input's front view, so the model's x-z silhouette (z-up,
reader on the -y side) is fitted to the object mask of the cleaned figure crop by
scale + offset (+ an optional mirror), maximising IoU. Label targets then map to
model coordinates and are cast onto the surface along +y.
"""
import numpy as np
from PIL import Image
from scipy import ndimage
from skimage.draw import polygon as fill_polygon


def white_ground_mask(img, white=235):
    """Object on a white page: everything that is not near-white, largest blob, holes filled."""
    g = np.asarray(img.convert("L"), float)
    m = ndimage.binary_opening(g < white, iterations=2)
    lab, n = ndimage.label(m)
    if n:
        m = lab == (np.argmax(np.bincount(lab.ravel())[1:]) + 1)
    return ndimage.binary_fill_holes(m)


def object_mask(img, border=30, thresh=28):
    """Object vs backdrop: model each row's backdrop from the left/right borders.

    For graded studio backdrops; on white pages use white_ground_mask, because a
    tight crop puts the object itself into the border columns.
    """
    a = np.asarray(img.convert("RGB"), float)
    # Skip white padding columns (the crop keeps a margin) so the backdrop estimate
    # comes from the photo's own edge, not from the page.
    cols = np.nonzero((a.mean(axis=2) < 250).mean(axis=0) > 0.05)[0]
    lo, hi = (cols.min(), cols.max() + 1) if len(cols) else (0, a.shape[1])
    bg = np.concatenate([a[:, lo:lo + border], a[:, hi - border:hi]], axis=1).mean(axis=1, keepdims=True)
    diff = np.abs(a - bg).max(axis=2)
    diff[:, :lo] = diff[:, hi:] = 0
    m = ndimage.binary_opening(diff > thresh, iterations=2)
    lab, n = ndimage.label(m)
    if n == 0:
        return m
    big = np.argmax(np.bincount(lab.ravel())[1:]) + 1
    return ndimage.binary_fill_holes(lab == big)


def grabcut_mask(img, iters=6):
    """GrabCut from a rectangle just inside the photo; good on the object body even
    when a dark backdrop leaks in around thin parts."""
    import cv2
    a = np.asarray(img.convert("RGB"))
    on = a.mean(axis=2) < 250
    cols, rows = np.nonzero(on.mean(axis=0) > 0.05)[0], np.nonzero(on.mean(axis=1) > 0.05)[0]
    x0, x1, y0, y1 = cols.min(), cols.max(), rows.min(), rows.max()
    rect = (int(x0 + 0.03 * (x1 - x0)), int(y0 + 0.01 * (y1 - y0)), int(0.94 * (x1 - x0)), int(0.98 * (y1 - y0)))
    mask = np.zeros(a.shape[:2], np.uint8)
    bgd, fgd = np.zeros((1, 65)), np.zeros((1, 65))
    cv2.grabCut(cv2.cvtColor(a, cv2.COLOR_RGB2BGR), mask, rect, bgd, fgd, iters, cv2.GC_INIT_WITH_RECT)
    fg = (mask == 1) | (mask == 3)
    lab, n = ndimage.label(fg)
    if n:
        fg = lab == (np.argmax(np.bincount(lab.ravel())[1:]) + 1)
    return ndimage.binary_fill_holes(fg)


def silhouette(mesh, shape, s, ox, oy, mirror=False, sz=None):
    """Rasterise the x-z projection: pixel u = (x - x0) s + ox, v = (z1 - z) sz + oy."""
    sz = s if sz is None else sz
    v = mesh.vertices
    x = -v[:, 0] if mirror else v[:, 0]
    x0, z1 = x.min(), v[:, 2].max()
    u, w = (x - x0) * s + ox, (z1 - v[:, 2]) * sz + oy
    out = np.zeros(shape, bool)
    for f in mesh.faces:
        rr, cc = fill_polygon(w[f], u[f], shape)
        out[rr, cc] = True
    return out


def _iou(a, b):
    return (a & b).sum() / max((a | b).sum(), 1)


def register(mesh, mask, work=256):
    """Best (s, ox, oy, mirror) mapping model x-z (mesh units) to mask pixels."""
    k = work / mask.shape[0]
    small = np.asarray(Image.fromarray(mask.astype(np.uint8) * 255).resize(
        (max(int(mask.shape[1] * k), 1), work), Image.NEAREST)) > 127
    ys, xs = np.nonzero(small)
    import trimesh
    best = None
    for yaw, mirror in [(y, m) for y in (0, 90, 180, 270) for m in (False, True)]:
        turned = mesh.copy().apply_transform(trimesh.transformations.rotation_matrix(np.deg2rad(yaw), [0, 0, 1]))
        v = turned.vertices
        height = v[:, 2].max() - v[:, 2].min()
        x = -v[:, 0] if mirror else v[:, 0]
        # Generated proportions drift (a Lux3D chime came back with a shorter body),
        # so width and height scale separately, each seeded from the mask's bbox.
        sz0 = (ys.max() - ys.min()) / height
        sx0 = (xs.max() - xs.min()) / max(np.ptp(x), 1e-9)
        for fx in (0.94, 0.97, 1.0, 1.03, 1.06):
            for fz in (0.96, 1.0, 1.04):
                sx, sz = sx0 * fx, sz0 * fz
                ox0 = xs.min() + (xs.max() - xs.min() - np.ptp(x) * sx) / 2
                oy0 = ys.max() - height * sz
                # Rasterise once per (yaw, mirror, scales); offsets are whole-pixel shifts.
                sil0 = silhouette(turned, small.shape, sx, ox0, oy0, mirror, sz)
                for dx in np.round(np.linspace(-0.03, 0.03, 7) * small.shape[1]).astype(int):
                    for dy in np.round(np.linspace(-0.02, 0.02, 5) * small.shape[0]).astype(int):
                        sil = np.roll(np.roll(sil0, dy, axis=0), dx, axis=1)
                        score = _iou(sil, small)
                        if best is None or score > best[0]:
                            best = (score, sx / k, sz / k, (ox0 + dx) / k, (oy0 + dy) / k, mirror, yaw)
    iou, s, sz, ox, oy, mirror, yaw = best
    return {"iou": float(iou), "s": s, "sz": sz, "ox": ox, "oy": oy, "mirror": mirror, "yaw": yaw, "method": "mask-iou"}


def render_front(mesh, colours, height_px=600, pad=4, mirror=False):
    """Orthographic textured front view (painter's order, nearest = smallest y last).

    Returns (rgb uint8, mask, px per mesh unit, pad); pixel convention as silhouette().
    """
    v = mesh.vertices
    x = -v[:, 0] if mirror else v[:, 0]
    x0, z0, z1 = x.min(), v[:, 2].min(), v[:, 2].max()
    s = (height_px - 2 * pad) / (z1 - z0)
    w = int(np.ceil((x.max() - x0) * s)) + 2 * pad
    u, vv = (x - x0) * s + pad, (z1 - v[:, 2]) * s + pad
    rgb = np.zeros((height_px, w, 3), np.uint8)
    mask = np.zeros((height_px, w), bool)
    depth = v[mesh.faces][:, :, 1].mean(axis=1)
    facing = mesh.face_normals[:, 1] < 0.2  # skip faces pointing away from the reader
    for f in np.argsort(-depth):
        if not facing[f]:
            continue
        rr, cc = fill_polygon(vv[mesh.faces[f]], u[mesh.faces[f]], mask.shape)
        rgb[rr, cc] = colours[f]
        mask[rr, cc] = True
    return rgb, mask, s, pad


def register_texture(mesh, colours, img, scales=np.linspace(0.55, 1.0, 19), work=200):
    """Template-match the textured front render against the figure crop.

    Needs no segmentation of the photo (a dark backdrop defeated both thresholding
    and GrabCut), because the Lux3D texture was baked from this very image. Both
    sides are matched small and blurred: per-face template colours carry no
    surface detail, so full-resolution décor only adds noise to the score.
    """
    import cv2
    import trimesh
    a = np.asarray(img.convert("RGB"))
    k_img = work / a.shape[0]
    small = cv2.resize(a, (int(a.shape[1] * k_img), work), interpolation=cv2.INTER_AREA)
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY).astype(np.float32), (0, 0), 1.5)
    best = None
    # Generated models do not share a front axis, so also search the yaw.
    for yaw, mirror in [(y, m) for y in (0, 90, 180, 270) for m in (False, True)]:
        rot = trimesh.transformations.rotation_matrix(np.deg2rad(yaw), [0, 0, 1])
        turned = mesh.copy().apply_transform(rot)
        rgb, m, s_t, pad = render_front(turned, (np.asarray(colours) * 255).astype(np.uint8), mirror=mirror)
        tg = cv2.GaussianBlur(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY).astype(np.float32), (0, 0), 1.5 * 600 / work)
        for k in scales:
            h = int(gray.shape[0] * k)
            f = h / tg.shape[0]
            wt = int(tg.shape[1] * f)
            if wt >= gray.shape[1] or h >= gray.shape[0]:
                continue
            t = cv2.resize(tg, (wt, h), interpolation=cv2.INTER_AREA)
            mk = cv2.resize(m.astype(np.uint8) * 255, (wt, h), interpolation=cv2.INTER_NEAREST)
            res = cv2.matchTemplate(gray, t, cv2.TM_CCOEFF_NORMED, mask=mk)
            res[~np.isfinite(res)] = -1
            _, score, _, loc = cv2.minMaxLoc(res)
            if best is None or score > best[0]:
                best = (score, s_t * f / k_img, (loc[0] + pad * f) / k_img, (loc[1] + pad * f) / k_img, mirror, yaw)
    score, s, ox, oy, mirror, yaw = best
    # Callers rotate the mesh by `yaw` (about +z) before casting or printing.
    return {"iou": float(score), "s": s, "ox": ox, "oy": oy, "mirror": mirror, "yaw": yaw, "method": "texture-ncc"}


def register_chamfer(mesh, img, scales=np.linspace(0.6, 1.0, 21), cap=25.0):
    """Fit the model's front outline to edges in the figure (chamfer distance).

    Per-face colours were too coarse for template NCC (it slid 100 px off), and
    the dark backdrop defeats segmentation; the outline against the backdrop
    still produces edges along most of its length.
    """
    import cv2
    import trimesh
    a = np.asarray(img.convert("RGB"))
    # Heavy blur first: surface décor (bosses, 纹饰) is all edges too and would let
    # a shrunken outline hide inside the object; only the silhouette survives.
    sigma = 0.012 * a.shape[0]
    gray = cv2.GaussianBlur(cv2.cvtColor(a, cv2.COLOR_RGB2GRAY), (0, 0), sigma)
    edges = cv2.Canny(gray, 8, 24) > 0
    dist = ndimage.distance_transform_edt(~edges)
    H, W = dist.shape
    best = None
    for yaw, mirror in [(y, m) for y in (0, 90, 180, 270) for m in (False, True)]:
        rot = trimesh.transformations.rotation_matrix(np.deg2rad(yaw), [0, 0, 1])
        turned = mesh.copy().apply_transform(rot)
        v = turned.vertices
        x = -v[:, 0] if mirror else v[:, 0]
        height = v[:, 2].max() - v[:, 2].min()
        base = silhouette(turned, (600, int(600 * np.ptp(x) / height) + 8), 592 / height, 4, 4, mirror)
        rim = base & ~ndimage.binary_erosion(base)
        ry, rx = np.nonzero(rim)
        for k in scales:
            f = H * k / 600
            py, px = ry * f, rx * f
            for oy in np.arange(0, H - py.max(), 6):
                for ox in np.arange(0, W - px.max(), 6):
                    d = dist[(py + oy).astype(int), (px + ox).astype(int)]
                    score = np.minimum(d, cap).mean()
                    if best is None or score < best[0]:
                        best = (score, 592 / height * f, 4 * f + ox, 4 * f + oy, mirror, yaw)
    score, s, ox, oy, mirror, yaw = best
    return {"iou": float(score), "s": s, "ox": ox, "oy": oy, "mirror": mirror, "yaw": yaw, "method": "chamfer"}


def cast(mesh, reg, uv, search=6.0):
    """Image point -> (face, point) on the reader-facing surface, or None.

    Misses right at the silhouette edge retry on a small ring around the point.
    """
    v = mesh.vertices
    x = -v[:, 0] if reg["mirror"] else v[:, 0]
    x0, z1 = x.min(), v[:, 2].max()
    u, w = uv
    mx = (u - reg["ox"]) / reg["s"] + x0
    mz = z1 - (w - reg["oy"]) / reg.get("sz", reg["s"])
    if reg["mirror"]:
        mx = -mx
    y0 = v[:, 1].min() - 1.0
    step = search / reg["s"]  # pixels -> mesh units
    offsets = [(0, 0)] + [(step * r * np.cos(t), step * r * np.sin(t))
                          for r in (0.5, 1, 2) for t in np.linspace(0, 2 * np.pi, 8, endpoint=False)]
    for dx, dz in offsets:
        locs, _, tri = mesh.ray.intersects_location([[mx + dx, y0, mz + dz]], [[0, 1, 0]], multiple_hits=False)
        if len(locs):
            return int(tri[0]), locs[0]
    return None
