"""Geometric part segmentation for tactile numbering (Lux3D exposes no part-split API).

Two cues cover most textbook objects: separate shells (bosses, handles, knobs come
out as their own bodies) and abrupt changes of the cross-section along the up axis
(a chime's shank vs its body, a bottle's neck vs its belly). Names are not guessed;
callers map them (for textbooks: from the figure's leader-line labels).
"""
import numpy as np
import trimesh


def _axial_cuts(mesh, bins=80, jump=2.5, min_len=0.12):
    """Heights where the cross-section area changes by more than `jump`x.

    Area is approximated by the horizontal footprint of vertices in each height
    band (convex-hull-free, robust to open shells). Bands shorter than `min_len`
    of the height are merged into neighbours so cuts stay meaningful.
    """
    v = mesh.vertices
    z0, z1 = v[:, 2].min(), v[:, 2].max()
    edges = np.linspace(z0, z1, bins + 1)
    width = np.zeros(bins)
    for i in range(bins):
        sel = (v[:, 2] >= edges[i]) & (v[:, 2] <= edges[i + 1])
        if sel.sum() > 3:
            width[i] = np.ptp(v[sel, 0]) * np.ptp(v[sel, 1])
    width += 1e-12
    cuts = []
    for i in range(1, bins):
        a, b = width[i - 1], width[i]
        if max(a, b) / min(a, b) > jump:
            z = edges[i]
            if not cuts or (z - cuts[-1]) > min_len * (z1 - z0):
                cuts.append(z)
    return [c for c in cuts if min_len * (z1 - z0) < c - z0 < (1 - min_len) * (z1 - z0)]


def segment(mesh, small=0.02):
    """Return parts as dicts {faces, kind, centre}; mesh must be z-up and welded.

    kind: "body" (axial band of the main shell) or "repeated" (many small shells
    grouped, e.g. the 36 bosses of a chime).
    """
    from scipy.spatial import cKDTree
    comps = mesh.split(only_watertight=False)
    areas = np.array([c.area for c in comps])
    total = areas.sum()
    # split() reorders faces; map each face back to its component via centroids.
    tree_pts = np.concatenate([c.triangles_center for c in comps])
    tree_lab = np.concatenate([np.full(len(c.faces), i) for i, c in enumerate(comps)])
    _, nn = cKDTree(tree_pts).query(mesh.triangles_center)
    face_ids = tree_lab[nn]

    parts = []
    main = int(np.argmax(areas))
    cuts = _axial_cuts(comps[main])
    zc = mesh.triangles_center[:, 2]
    bounds = [-np.inf, *cuts, np.inf]
    for lo, hi in zip(bounds[:-1], bounds[1:]):
        faces = np.nonzero((face_ids == main) & (zc >= lo) & (zc < hi))[0]
        if len(faces):
            parts.append({"faces": faces, "kind": "body"})
    others = [i for i in range(len(comps)) if i != main]
    tiny = [i for i in others if areas[i] < small * total]
    for i in others:
        if i not in tiny:
            parts.append({"faces": np.nonzero(face_ids == i)[0], "kind": "body"})
    if tiny:
        parts.append({"faces": np.nonzero(np.isin(face_ids, tiny))[0], "kind": "repeated", "count": len(tiny)})
    for p in parts:
        p["centre"] = mesh.triangles_center[p["faces"]].mean(axis=0)
    # Top to bottom, so callers can name them in reading order of a figure.
    parts.sort(key=lambda p: -p["centre"][2])
    return parts


def label_site(mesh, faces, toward_reader=(0, -1, 0), radius=6.0, avoid=(), spacing=12.0):
    """Flattest reader-facing spot on a part: (point, normal) in mesh units (mm).

    The reader sits on the `toward_reader` side (front = -y here). Faces score by
    how directly their normal points that way and how little normals vary within
    `radius`, so braille sits on a calm patch rather than an edge. Sites closer
    than `spacing` to any point in `avoid` (labels already placed) are skipped.
    """
    avoid = np.asarray(avoid, float).reshape(-1, 3)
    from scipy.spatial import cKDTree
    c, n = mesh.triangles_center[faces], mesh.face_normals[faces]
    facing = n @ np.asarray(toward_reader, float)
    tree = cKDTree(c)
    best, best_score = None, -np.inf
    cand = faces[np.argsort(-facing)[: min(1500, len(faces))]]
    for f in cand:
        if len(avoid) and np.linalg.norm(avoid - mesh.triangles_center[f], axis=1).min() < spacing:
            continue
        nb = tree.query_ball_point(mesh.triangles_center[f], radius)
        if len(nb) < 4:
            continue
        nn = n[nb]
        calm = np.linalg.norm(nn.mean(axis=0))  # 1 = perfectly flat patch
        score = 2 * calm + (mesh.face_normals[f] @ np.asarray(toward_reader, float))
        if score > best_score:
            best, best_score = f, score
    if best is None:
        best = cand[0]
    nb = np.asarray(tree.query_ball_point(mesh.triangles_center[best], radius))
    own = mesh.face_normals[best]
    # Average only like-facing neighbours: on thin shanks or grouped bosses the
    # patch can contain opposite faces whose normals cancel out.
    nb = nb[n[nb] @ own > 0.5] if len(nb) else nb
    normal = n[nb].mean(axis=0) if len(nb) else own
    length = np.linalg.norm(normal)
    return mesh.triangles_center[best], normal / length if length > 1e-6 else own


def label_regions(mesh, parts, hits, attach=0.12, repeated_labels=()):
    """Turn figure labels into named face regions.

    hits: [(label, face, point)] from casting leader targets onto the model.
    - a label on a "repeated" part names the whole group (one boss -> all 33 枚);
    - several labels on one body part split it by nearest hit point (钲 vs 鼓);
    - unlabelled parts join the nearest hit if within `attach` of the model height
      (shank pieces join 甬), otherwise stay unnumbered;
    - labels in `repeated_labels` (e.g. 枚) move onto the nearest repeated part: the
      figure's details rarely sit where the generated model put them, and plain
      distance picked the wrong label (钲 was 5 mm from a boss, 枚 7 mm), so a
      person says which label means the repeated detail.
    Returns [(label, faces)] in the order of `hits`.
    """
    part_of = np.full(len(mesh.faces), -1)
    for i, p in enumerate(parts):
        part_of[p["faces"]] = i
    centres = mesh.triangles_center
    pts = np.array([h[2] for h in hits])
    height = np.ptp(mesh.vertices[:, 2])
    hits = list(hits)
    rep = [p for p in parts if p["kind"] == "repeated"]
    for k, (label, face, point) in enumerate(hits):
        if label in repeated_labels and rep and parts[part_of[face]]["kind"] != "repeated":
            d = [np.linalg.norm(centres[p["faces"]] - point, axis=1).min() for p in rep]
            p = rep[int(np.argmin(d))]
            hits[k] = (label, int(p["faces"][0]), point)
    regions = [[] for _ in hits]
    for i, p in enumerate(parts):
        mine = [k for k, h in enumerate(hits) if part_of[h[1]] == i]
        if not mine:
            d = np.linalg.norm(pts - p["centre"], axis=1)
            k = int(np.argmin(d))
            if d[k] < attach * height and p["kind"] != "repeated":
                regions[k].extend(p["faces"])
            continue
        if len(mine) == 1 or p["kind"] == "repeated":
            regions[mine[0]].extend(p["faces"])
            continue
        d = np.linalg.norm(centres[p["faces"]][:, None, :] - pts[mine][None], axis=2)
        nearest = np.array(mine)[np.argmin(d, axis=1)]
        for k in mine:
            regions[k].extend(p["faces"][nearest == k])
    return [(h[0], np.asarray(r, int)) for h, r in zip(hits, regions)]
