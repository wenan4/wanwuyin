"""Shared seal metadata; glTF is y-up and the plan view is the x-z plane."""
import json
import os
import re
import shutil
import tempfile
import time
from pathlib import Path

ROUND = 1.12
ORDER = ["name", "label", "cells", "why", "real", "glb", "yaw"]


def plan_yaw(mesh, samples=200_000, seed=0):
    import numpy as np
    import trimesh

    pts, _ = trimesh.sample.sample_surface(mesh, samples, seed=seed)
    xz = pts[:, [0, 2]] - pts[:, [0, 2]].mean(0)
    w, v = np.linalg.eigh(np.cov(xz.T))
    if w[0] <= 0:
        raise ValueError("Model has no usable three-dimensional surface")
    aspect = float(np.sqrt(w[1] / w[0]))
    phi = float(np.arctan2(v[1, 1], v[0, 1]))
    phi = (phi + np.pi / 2) % np.pi - np.pi / 2
    return (0.0 if aspect < ROUND else round(phi, 3)), aspect


def check_runtime():
    import trimesh  # noqa: F401
    from .tactile import to_braille  # noqa: F401

    if not shutil.which("lou_translate"):
        raise RuntimeError("Install liblouis (lou_translate with zhcn-cbs.ctb) first")
    to_braille("大象")  # Check the tables before any paid upstream task is created.


def metadata(glb, label):
    import trimesh
    from .tactile import to_braille

    yaw, aspect = plan_yaw(trimesh.load(glb, force="mesh"))
    cells = to_braille(label)
    if not cells or any(not 0x2800 <= ord(c) <= 0x283F for c in cells):
        raise ValueError("The label did not translate into six-dot braille")
    return {"yaw": yaw, "aspect": round(aspect, 2), "cells": cells}


def write_seals(path, seals):
    """Replace a manifest atomically, preserving the one-entry-per-line layout."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    body = ",\n".join("  " + json.dumps(s, ensure_ascii=False) for s in seals)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(f"[\n{body}\n]\n")
            f.flush()
            os.fsync(f.fileno())
        # Windows readers may briefly hold the old manifest without FILE_SHARE_DELETE.
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                break
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(min(0.01 * (attempt + 1), 0.05))
    finally:
        Path(tmp).unlink(missing_ok=True)


def upsert(path, name, glb, meta, label=None, why=None, real=None):
    """For offline editing. The service publishes its manifest from SQLite instead."""
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", name):
        raise ValueError("name must contain only ASCII letters, digits, _ or -")
    path = Path(path)
    seals = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    i = next((k for k, s in enumerate(seals) if s["name"] == name), None)
    if i is None:
        missing = [f"--{k}" for k, v in (("label", label), ("why", why), ("real", real)) if not v]
        if missing:
            raise ValueError(f"{name} is a new seal: also give {' '.join(missing)}")
        seals.append({"name": name})
        i = len(seals) - 1
    dest = path.parent / "objects" / name / "model.glb"
    if Path(glb).resolve() != dest.resolve():
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(glb, dest)
    entry = dict(seals[i], glb=f"objects/{name}/model.glb", yaw=meta["yaw"])
    entry.update({k: v for k, v in (("label", label), ("why", why), ("real", real),
                                  ("cells", meta.get("cells"))) if v})
    seals[i] = {k: entry[k] for k in ORDER if k in entry} | {k: v for k, v in entry.items() if k not in ORDER}
    write_seals(path, seals)
    return seals[i]
