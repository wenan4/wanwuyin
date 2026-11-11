"""Parse a labelled textbook figure: caption, labels, leader lines, clean object crop.

Leader lines are found with a Hough transform on near-black pixels, erased, and
then the labels are read by the local Vision OCR tool (tools/ocr). Each label owns
the collinear line whose end is next to it; the far end (snapped to a dot if there
is one) is the point the label names. Tested on a synthetic figure with pure-black
leaders; phone photos of printed pages will need a tolerant threshold or a
teacher's tap to confirm targets.
"""
import json
import re
import subprocess
from pathlib import Path

import numpy as np
from PIL import Image
from scipy import ndimage
from skimage.draw import line as draw_line
from skimage.transform import probabilistic_hough_line

OCR = Path(__file__).resolve().parents[1] / "tools" / "ocr"


def ocr(path):
    out = subprocess.run([str(OCR), str(path)], capture_output=True, text=True, check=True).stdout
    return json.loads(out)


def _cluster_lines(segs, angle_tol=np.deg2rad(2.5), offset_tol=8.0):
    """Merge collinear Hough segments; return each line's two extreme end points."""
    lines = []
    for (x0, y0), (x1, y1) in segs:
        p, q = np.array([x0, y0], float), np.array([x1, y1], float)
        d = (q - p) / (np.linalg.norm(q - p) + 1e-9)
        n = np.array([-d[1], d[0]])
        for ln in lines:
            if abs(abs(d @ ln["d"]) - 1) < 1 - np.cos(angle_tol) and abs(n @ (p - ln["p0"])) < offset_tol:
                ln["pts"] += [p, q]
                break
        else:
            lines.append({"p0": p, "d": d, "pts": [p, q]})
    out = []
    for ln in lines:
        pts = np.array(ln["pts"])
        t = (pts - ln["p0"]) @ ln["d"]
        out.append((pts[np.argmin(t)], pts[np.argmax(t)]))
    return out


def _box_distance(pt, box):
    x0, y0, x1, y1 = box
    dx = max(x0 - pt[0], 0, pt[0] - x1)
    dy = max(y0 - pt[1], 0, pt[1] - y1)
    return float(np.hypot(dx, dy))


def _fill(img, hole, iters=12):
    """Cheap inpainting for thin strokes: grow inwards with neighbour means."""
    a = img.astype(float)
    known = ~hole
    k = np.ones((3, 3))
    for _ in range(iters):
        if known.all():
            break
        s = np.stack([ndimage.convolve(a[..., c] * known, k, mode="nearest") for c in range(3)], -1)
        n = ndimage.convolve(known.astype(float), k, mode="nearest")
        grow = ~known & (n > 0)
        a[grow] = s[grow] / n[grow][:, None]
        known = known | grow
    return a.astype(np.uint8)


def parse(path, black=8, white=245, near=60):
    img = np.asarray(Image.open(path).convert("RGB"))
    g = img.mean(axis=2)
    dark = g < black
    segs = probabilistic_hough_line(dark, threshold=20, line_length=120, line_gap=10, rng=0)
    lines = _cluster_lines(segs)
    line_mask = np.zeros(dark.shape, bool)
    for (x0, y0), (x1, y1) in segs:
        rr, cc = draw_line(int(y0), int(x0), int(y1), int(x1))
        line_mask[rr, cc] = True
    line_mask = ndimage.binary_dilation(line_mask, iterations=4) & dark
    # End dots: small near-black blobs left after removing the lines.
    blobs, n_blobs = ndimage.label(ndimage.binary_opening(dark & ~line_mask, iterations=2))
    dots = ndimage.center_of_mass(dark, blobs, range(1, n_blobs + 1)) if n_blobs else []

    no_lines = img.copy()
    no_lines[ndimage.binary_dilation(line_mask, iterations=1)] = 255
    tmp = Path(path).with_suffix(".nolines.png")
    Image.fromarray(no_lines).save(tmp)
    texts = ocr(tmp)
    tmp.unlink()

    caption, labels = None, []
    for t in texts:
        if re.match(r"^\s*图\s*\d", t["text"]) or len(t["text"].strip()) > 3:
            caption = t
        else:
            labels.append({"text": t["text"].strip(), "box": t["box"], "conf": t["conf"]})
    for lab in labels:
        best = None
        for a, b in lines:
            da, db = _box_distance(a, lab["box"]), _box_distance(b, lab["box"])
            if min(da, db) < near:
                far, length = (b if da < db else a), np.linalg.norm(b - a)
                if best is None or length > best[1]:
                    best = (far, length)
        if best is None:
            lab["target"] = None
            continue
        far = best[0]
        if dots:
            dd = [np.hypot(cy - far[1], cx - far[0]) for cy, cx in dots]
            i = int(np.argmin(dd))
            if dd[i] < 15:
                far = np.array([dots[i][1], dots[i][0]])
        lab["target"] = [float(far[0]), float(far[1])]

    # Clean figure: thin strokes over the photo are inpainted; labels sit in the white
    # margins of textbook figures, so their boxes are simply painted white.
    hole = ndimage.binary_dilation(line_mask, iterations=2)
    for cy, cx in dots:
        hole[int(cy) - 10:int(cy) + 11, int(cx) - 10:int(cx) + 11] = True
    clean = _fill(img, hole)
    for t in texts:
        x0, y0, x1, y1 = (int(v) for v in t["box"])
        clean[max(y0 - 14, 0):y1 + 14, max(x0 - 14, 0):x1 + 14] = 255
    obj = clean.mean(axis=2) < white
    obj = ndimage.binary_opening(obj, iterations=3)
    ys, xs = np.nonzero(obj)
    pad = 20
    crop = [max(xs.min() - pad, 0), max(ys.min() - pad, 0), min(xs.max() + pad, img.shape[1]), min(ys.max() + pad, img.shape[0])]
    title = re.sub(r"^\s*图\s*\d+\s*", "", caption["text"]).strip() if caption else None
    return {"title": title, "caption": caption, "labels": labels, "crop": crop,
            "clean": Image.fromarray(clean).crop(crop), "lines": len(lines)}
