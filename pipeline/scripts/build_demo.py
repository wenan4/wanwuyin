"""Build demo objects: modal analysis -> WAV renders -> web viewer data.

  python scripts/build_demo.py                          # procedural bowl/bell/cup/bar
  python scripts/build_demo.py --glb out/lux3d/bowl/output_0.glb --name lux_bowl \
      --label "Lux3D 瓷碗" --material porcelain --size 0.14

Web data goes to ../web/public/objects/<name>/{model.glb, modes.bin, meta.json}.
Model GLBs stay in their own y-up frame and vertex order; mode vectors are
rotated back into that frame so the viewer can apply them to the loaded geometry.
"""
import argparse
import json
import shutil
import sys
import time
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from wwys import fem, materials, shapes, synth  # noqa: E402

WEB = ROOT.parent / "web" / "public"
AUDIO = ROOT / "out" / "audio"
Y_UP_TO_Z_UP = trimesh.transformations.rotation_matrix(np.pi / 2, [1, 0, 0])
COMPARE = ["porcelain", "bronze", "glass", "wood", "steel"]
# 茉莉花 opening phrase as (MIDI note, beats): mi mi sol la do' do' la sol | sol la sol
JASMINE = [(76, .5), (76, .25), (79, .25), (81, .25), (84, .25), (84, .25), (81, .25), (79, .5),
           (79, .25), (81, .25), (79, 1.0)]


def first_audible_mode(freqs, amps):
    """Index of the lowest mode the strike actually excites: our pitch reference."""
    strong = np.abs(amps) > 0.2 * np.abs(amps).max()
    return int(np.argmax(strong))


def analyse(name, mesh_zup, label, material, size_m, strikes, glb_src=None, wall_m=None, budget=180000,
            variants=()):
    """strikes: [(display name, shapes.strike_vertex hint)], first one is the default."""
    t0 = time.time()
    mesh_norm = fem.normalize_mesh(mesh_zup)
    wall = wall_m / size_m if wall_m else None  # normalised units
    model = fem.ModalModel.from_mesh(mesh_norm, wall=wall, budget=budget, log=lambda s: print(f"[{name}] {s}"))
    p = materials.params(material)
    freqs = model.frequencies(p["E"], p["rho"], size_m)
    print(f"[{name}] {model.n_voxels} voxels, N={round(1 / model.pitch)}, {time.time() - t0:.0f}s; "
          f"f[:6] = {np.round(freqs[:6]).astype(int).tolist()} Hz ({p['name']}, {size_m * 100:.0f} cm)")

    points = [(label_, shapes.strike_vertex(mesh_norm, hint)) for label_, hint in strikes]
    sv = points[0][1]
    phi_v = model.sample(mesh_norm.vertices[sv:sv + 1])[0]
    normal = mesh_norm.vertex_normals[sv]

    AUDIO.mkdir(parents=True, exist_ok=True)
    for key in COMPARE:
        q = materials.params(key)
        f = model.frequencies(q["E"], q["rho"], size_m)
        y = synth.render(f, materials.decay_rates(f, key), synth.strike_amplitudes(phi_v, normal, f))
        synth.write_wav(AUDIO / f"{name}_{key}.wav", y)
    if len(points) > 1:  # e.g. 正鼓 / 侧鼓 on a chime: same bell, different note
        for i, (label_, v) in enumerate(points):
            a = synth.strike_amplitudes(model.sample(mesh_norm.vertices[v:v + 1])[0], mesh_norm.vertex_normals[v], freqs)
            k = int(np.argmax(np.abs(a)))
            print(f"[{name}] {label_}: strongest mode {freqs[k]:.0f} Hz")
            synth.write_wav(AUDIO / f"{name}_strike{i}.wav", synth.render(freqs, materials.decay_rates(freqs, material), a))

    export_web(name, mesh_zup, mesh_norm, model, label, material, size_m, points, glb_src, wall_m, variants)
    return model, mesh_norm, phi_v, normal


def render_melody(name, model, phi_v, normal, material, size_m):
    """Same shape, resized per note: the scaling law f ∝ 1/size does the tuning."""
    p = materials.params(material)
    f_ref = model.frequencies(p["E"], p["rho"], size_m)
    amps = synth.strike_amplitudes(phi_v, normal, f_ref)
    k = first_audible_mode(f_ref, amps)
    targets = [440 * 2 ** ((m - 69) / 12) for m, _ in JASMINE]
    # Shift by octaves so every bowl lands between 8 and 40 cm.
    sizes = [size_m * f_ref[k] / t for t in targets]
    while max(sizes) > 0.40:
        sizes = [s / 2 for s in sizes]
    while min(sizes) < 0.08:
        sizes = [s * 2 for s in sizes]
    clips, onsets, t, beat = [], [], 0.0, 0.42
    for s, (_, beats) in zip(sizes, JASMINE):
        f = model.frequencies(p["E"], p["rho"], s)
        y = synth.render(f, materials.decay_rates(f, material), synth.strike_amplitudes(phi_v, normal, f))
        clips.append(synth.fade_out(y[: int(44100 * 2.5)], 44100))
        onsets.append(t)
        t += beats * beat * 2
    synth.write_wav(AUDIO / f"{name}_jasmine.wav", synth.sequence(clips, onsets))
    uniq = sorted({round(s * 100, 1) for s in sizes}, reverse=True)
    print(f"[{name}] 茉莉花 sizes (cm): {uniq}")


def smooth_on_mesh(mesh, phi, iters=3):
    """Average mode shapes over welded mesh neighbours to remove voxel-scale jitter.

    Interpolating from the voxel lattice leaves noise that the exaggerated
    slow-motion display turns into a crinkled surface.
    """
    import scipy.sparse as sp
    w = mesh.copy()
    w.merge_vertices(merge_tex=True, merge_norm=True)
    e = w.edges_unique
    n = len(w.vertices)
    A = sp.coo_matrix((np.ones(2 * len(e)), (np.r_[e[:, 0], e[:, 1]], np.r_[e[:, 1], e[:, 0]])), shape=(n, n)).tocsr()
    deg = np.asarray(A.sum(axis=1)).ravel()
    deg[deg == 0] = 1
    # UV-seam duplicates share a welded vertex: average them in, smooth, scatter back.
    key = {tuple(np.round(v, 7)): i for i, v in enumerate(w.vertices)}
    to_w = np.array([key.get(tuple(np.round(v, 7)), -1) for v in mesh.vertices])
    if (to_w < 0).any():
        return phi  # welding changed positions; skip rather than mis-assign
    flat = phi.reshape(len(phi), -1)
    acc = np.zeros((n, flat.shape[1]))
    np.add.at(acc, to_w, flat)
    cnt = np.bincount(to_w, minlength=n)[:, None]
    q = acc / np.maximum(cnt, 1)
    for _ in range(iters):
        q = 0.5 * q + 0.5 * (A @ q) / deg[:, None]
    return q[to_w].reshape(phi.shape)


def export_web(name, mesh_zup, mesh_norm, model, label, material, size_m, points, glb_src, wall_m=None,
               variants=()):
    out = WEB / "objects" / name
    out.mkdir(parents=True, exist_ok=True)
    # Lux3D material-transfer outputs keep the vertex order, so they reuse modes.bin.
    variant_meta = []
    for i, (vlabel, vpath, vmat) in enumerate(variants):
        vm = trimesh.load(vpath, force="mesh", process=False)
        if len(vm.vertices) != len(mesh_zup.vertices):
            raise ValueError(f"variant {vpath} has {len(vm.vertices)} vertices, base has {len(mesh_zup.vertices)}")
        shutil.copy(vpath, out / f"variant_{i}.glb")
        variant_meta.append({"label": vlabel, "glb": f"variant_{i}.glb", "material": vmat})
    if glb_src:
        shutil.copy(glb_src, out / "model.glb")
    else:
        # Procedural meshes: subdivide so the deformation reads smoothly, export y-up.
        disp = trimesh.Trimesh(*trimesh.remesh.subdivide_to_size(
            mesh_norm.vertices, mesh_norm.faces, max_edge=shapes.DISPLAY_EDGE.get(name, 0.025))[:2])
        if len(disp.vertices) > 12000:  # display only: keeps modes.bin small (~6 MB)
            disp = disp.simplify_quadric_decimation(face_count=24000)
            trimesh.smoothing.filter_taubin(disp, iterations=10)  # decimation leaves slivers
        src = mesh_norm.vertices
        points = [(n, int(np.argmin(np.linalg.norm(disp.vertices - src[v], axis=1)))) for n, v in points]
        mesh_norm = disp
        disp.copy().apply_transform(np.linalg.inv(Y_UP_TO_Z_UP)).export(out / "model.glb")
    phi = smooth_on_mesh(mesh_norm, model.sample(mesh_norm.vertices))  # (P, 3, m), z-up frame
    phi_yup = np.einsum("ij,pjm->pim", np.linalg.inv(Y_UP_TO_Z_UP)[:3, :3], phi)
    phi_yup.transpose(2, 0, 1).astype(np.float32).tofile(out / "modes.bin")  # [mode][vertex][xyz]
    # Normal component at each strike point lets the listening page synthesise the
    # sound without loading any geometry.
    strike_points = [{"name": n, "vertex": v, "shape": (mesh_norm.vertex_normals[v] @ phi[v]).tolist()}
                     for n, v in points]
    meta = {
        "name": name, "label": label, "material": material, "sizeM": size_m,
        "pitch": model.pitch, "lambdaUnit": model.lam_unit.tolist(),
        "vertexCount": len(mesh_norm.vertices), "modeCount": len(model.lam_unit),
        "strikeVertex": points[0][1], "source": "lux3d" if glb_src else "procedural",
        "wallMm": wall_m * 1000 if wall_m else None, "voxels": model.n_voxels,
        "strikePoints": strike_points, "variants": variant_meta,
    }
    (out / "meta.json").write_text(json.dumps(meta, ensure_ascii=False))


def update_manifest():
    objs = []
    for d in sorted((WEB / "objects").iterdir()):
        if (d / "meta.json").exists():
            m = json.loads((d / "meta.json").read_text())
            objs.append({"name": m["name"], "label": m["label"]})
    (WEB / "objects" / "manifest.json").write_text(json.dumps(objs, ensure_ascii=False))
    materials.write_json(WEB / "materials.json")


def load_glb_zup(path):
    """Load a single-primitive GLB without reordering vertices, rotated to z-up."""
    mesh = trimesh.load(path, force="mesh", process=False)
    return mesh.apply_transform(Y_UP_TO_Z_UP)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--glb")
    ap.add_argument("--name")
    ap.add_argument("--label")
    ap.add_argument("--material", default="porcelain", help="material key, or auto (reviewed > recognised)")
    ap.add_argument("--size", type=float, default=0.14, help="real max extent in metres")
    ap.add_argument("--strike", action="append", default=[],
                    help="name=hint, repeatable; hints: rim, lip, end, zhenggu, cegu (first is default)")
    ap.add_argument("--wall-mm", type=float, help="thicken generated shells to this wall (mm)")
    ap.add_argument("--budget", type=int, default=180000, help="voxel budget (~180k is about a minute; larger grows fast)")
    ap.add_argument("--variant", action="append", default=[],
                    help="label=GLB of a Lux3D material repaint (material read from its material.json)")
    ap.add_argument("--only", nargs="*", help="subset of procedural demo names")
    args = ap.parse_args()

    if args.glb:
        if args.material == "auto":
            # Reviewed label (material.json next to the GLB) wins over the automatic guess.
            import material_review
            args.material, how = material_review.resolve(Path(args.glb).parent, args.label)
            print(f"[{args.name}] material {args.material} ({how})")
        mesh = load_glb_zup(args.glb)
        print(f"[{args.name}] {len(mesh.vertices)} verts, watertight={mesh.is_watertight}")
        import material_review
        variants = []
        for v in args.variant:
            vlabel, vpath = v.split("=", 1)
            vmat, how = material_review.resolve(Path(vpath).parent)
            variants.append((vlabel, vpath, vmat))
            print(f"[{args.name}] variant {vlabel}: {vmat} ({how})")
        model, mesh_norm, phi_v, n = analyse(args.name, mesh, args.label or args.name, args.material,
                                             args.size, [tuple(s.split("=", 1)) for s in args.strike] or [("默认", "rim")],
                                             glb_src=args.glb,
                                             wall_m=args.wall_mm / 1000 if args.wall_mm else None,
                                             budget=args.budget, variants=variants)
        render_melody(args.name, model, phi_v, n, args.material, args.size)
    else:
        for name, (build, label, material, size, hint) in shapes.DEMO.items():
            if args.only and name not in args.only:
                continue
            strikes = hint if isinstance(hint, list) else [("默认", hint)]
            model, mesh_norm, phi_v, n = analyse(name, build(), label, material, size, strikes)
            if name == "bowl":
                render_melody(name, model, phi_v, n, material, size)
    update_manifest()


if __name__ == "__main__":
    main()
