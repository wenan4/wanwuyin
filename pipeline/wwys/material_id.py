"""Acoustic material recognition from Lux3D PBR textures (no external model).

Each face gets features sampled from the base-colour and metallic-roughness maps
(glTF: G = roughness, B = metallic). Rule scores per material are combined with an
optional text prior (prompt / label keywords), smoothed over face adjacency, and
grouped into material regions. A VLM can later replace `face_scores` behind the
same interface.
"""
import numpy as np
from skimage.color import rgb2hsv, rgb2lab

LABELS = ["porcelain", "glass", "bronze", "steel", "aluminum", "stone", "wood", "plastic"]
KEYWORDS = {
    "porcelain": ["瓷", "陶", "碗", "盘", "杯盏", "porcelain", "ceramic"],
    "glass": ["玻璃", "glass"],
    "bronze": ["青铜", "铜", "编钟", "甬钟", "钟", "鼎", "bronze", "brass"],
    "steel": ["钢", "铁", "锅", "steel", "iron"],
    "aluminum": ["铝", "aluminum"],
    "stone": ["石", "玉", "stone", "marble"],
    "wood": ["木", "竹", "wood", "bamboo"],
    "plastic": ["塑料", "plastic"],
}


def _sigmoid(x):
    return 1 / (1 + np.exp(-x))


def _sample(img, uv):
    """Nearest-texel lookup; trimesh already flips glTF v so row = (1 - v) * H."""
    a = np.asarray(img.convert("RGBA"), dtype=np.float32) / 255
    h, w = a.shape[:2]
    x = np.clip((uv[:, 0] % 1) * (w - 1), 0, w - 1).astype(int)
    y = np.clip((1 - uv[:, 1] % 1) * (h - 1), 0, h - 1).astype(int)
    return a[y, x]


def face_features(mesh, samples=6, seed=0):
    """Per-face mean colour / metallic / roughness plus within-face colour spread."""
    mat, uv, f = mesh.visual.material, mesh.visual.uv, mesh.faces
    rng = np.random.default_rng(seed)
    bary = rng.dirichlet(np.ones(3), size=samples)  # same sample pattern for every face
    fuv = np.einsum("sk,fkd->fsd", bary, uv[f]).reshape(-1, 2)
    base = _sample(mat.baseColorTexture, fuv).reshape(len(f), samples, 4)
    factor = np.asarray(mat.baseColorFactor, float)[:3] / 255
    rgb = base[..., :3] * factor
    if mat.metallicRoughnessTexture is not None:
        mr = _sample(mat.metallicRoughnessTexture, fuv).reshape(len(f), samples, 4)
        rough, metal = mr[..., 1] * mat.roughnessFactor, mr[..., 2] * mat.metallicFactor
    else:
        rough = np.full(rgb.shape[:2], mat.roughnessFactor or 1.0)
        metal = np.full(rgb.shape[:2], mat.metallicFactor or 0.0)
    mean = rgb.mean(axis=1)
    hsv = rgb2hsv(mean[None])[0]
    lab = rgb2lab(mean[None])[0]
    return {
        "rgb": mean, "hue": hsv[:, 0] * 360, "sat": hsv[:, 1], "light": lab[:, 0] / 100,
        "lab": lab, "rough": rough.mean(1), "metal": metal.mean(1),
        "busy": rgb.std(axis=1).mean(1), "alpha": base[..., 3].mean(1),
    }


def face_scores(ft):
    """Unnormalised per-face material scores (faces x LABELS) from hand-set rules."""
    m, r, L, s, h = ft["metal"], ft["rough"], ft["light"], ft["sat"], ft["hue"]
    # Lux3D's metallic channel is noisy (a polished wooden carving came back at 0.43),
    # so metal needs > 0.5 and vivid warm saturation pulls towards wood.
    metal = _sigmoid((m - 0.5) / 0.06)
    warm = (h > 12) & (h < 50)
    patina = (h > 90) & (h < 190) & (s > 0.15)
    vivid_wood = warm * _sigmoid((s - 0.55) / 0.06)
    sc = {
        "bronze": metal * (0.3 + 0.7 * (warm | patina)) * (1 - 0.6 * vivid_wood),
        "steel": metal * (1 - 0.7 * (warm | patina)) * _sigmoid((0.75 - L) / 0.08),
        "aluminum": metal * (1 - 0.7 * (warm | patina)) * _sigmoid((L - 0.7) / 0.06),
        "porcelain": (1 - metal) * _sigmoid((L - 0.6) / 0.06) * _sigmoid((0.45 - r) / 0.08),
        "glass": (1 - metal) * _sigmoid((0.9 - ft["alpha"]) / 0.03) + 0.05 * (1 - metal) * _sigmoid((0.15 - r) / 0.04),
        "stone": (1 - metal) * _sigmoid((r - 0.55) / 0.08) * _sigmoid((0.38 - s) / 0.06),
        "wood": (1 - metal * (1 - vivid_wood)) * warm * _sigmoid((s - 0.42) / 0.06) * _sigmoid((r - 0.35) / 0.08),
        "plastic": (1 - metal) * (0.15 + 0.85 * _sigmoid((s - 0.5) / 0.06)) * (1 - warm * 0.7),
    }
    return np.stack([sc[k] for k in LABELS], axis=1) + 1e-6


def text_prior(text):
    """Multiplicative prior from keywords in a prompt or label (1 = no opinion)."""
    w = np.ones(len(LABELS))
    for i, k in enumerate(LABELS):
        if text and any(kw in text for kw in KEYWORDS[k]):
            w[i] = 4.0
    return w


def smooth(mesh, prob, iters=4):
    """Average class probabilities over face neighbours to clean texture noise."""
    adj = mesh.face_adjacency
    for _ in range(iters):
        acc = prob.copy()
        np.add.at(acc, adj[:, 0], prob[adj[:, 1]])
        np.add.at(acc, adj[:, 1], prob[adj[:, 0]])
        prob = acc / acc.sum(axis=1, keepdims=True)
    return prob


def recognise(mesh, text=None, min_region=0.05):
    """Return per-face labels, probabilities and a region summary (area-weighted)."""
    ft = face_features(mesh)
    prob = face_scores(ft) * text_prior(text)
    prob /= prob.sum(axis=1, keepdims=True)
    welded = mesh.copy()
    welded.merge_vertices(merge_tex=True, merge_norm=True)  # adjacency across UV seams
    prob = smooth(welded, prob)
    labels = prob.argmax(axis=1)
    area = mesh.area_faces / mesh.area_faces.sum()
    regions = []
    for i, k in enumerate(LABELS):
        sel = labels == i
        a = float(area[sel].sum())
        if a >= min_region:
            regions.append({
                "material": k, "area": round(a, 3),
                "confidence": round(float((prob[sel, i] * area[sel]).sum() / a), 2),
                "metal": round(float(ft["metal"][sel].mean()), 2), "rough": round(float(ft["rough"][sel].mean()), 2),
                "rgb": [round(float(c), 2) for c in ft["rgb"][sel].mean(0)],
            })
    regions.sort(key=lambda r: -r["area"])
    # Faces in sub-threshold regions fall back to the dominant material.
    keep = [LABELS.index(r["material"]) for r in regions]
    labels = np.where(np.isin(labels, keep), labels, keep[0])
    return {"material": regions[0]["material"], "regions": regions, "face_labels": labels, "prob": prob}
