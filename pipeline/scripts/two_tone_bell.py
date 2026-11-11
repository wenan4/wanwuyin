"""Does an almond (合瓦形) cross-section give a chime its two tones? A physics check.

Builds bells whose horizontal section is a lens (two intersecting arcs) straight
on the voxel grid, then compares the dominant pitch when struck at 正鼓 (front
centre) and 侧鼓 (on the 正鼓 mode's node line). Real 曾侯乙 chimes sit about a
minor third (3 semitones) apart; a round section should give ~0.

  python scripts/two_tone_bell.py
"""
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys import fem, materials, synth  # noqa: E402

from wwys.shapes import lens_bell_occupancy  # noqa: E402

H, A_TOP, A_MOUTH = 0.30, 0.085, 0.115  # metres, as in lens_bell_occupancy defaults


def dominant(phi_at, normal, freqs):
    amps = synth.strike_amplitudes(phi_at, normal, freqs)
    return int(np.argmax(np.abs(amps)))


def main(h=0.0035):
    p = materials.params("bronze")
    print(f"bronze lens-section bell, {H * 100:.0f} cm tall, 15 mm wall, voxel {h * 1000:.1f} mm")
    # 8 mm walls let the flat faces flap at low pitch and swamp the 正鼓 tone; 15 mm is chime-like.
    for ratio in (1.0, 0.8, 0.65):
        occ, x, y, z = lens_bell_occupancy(ratio, h, wall=0.015, top=0.02)
        lam, phi, nodes = fem.solve_modes(occ, n_modes=16)
        freqs = np.sqrt(lam * p["E"] / p["rho"]) / (2 * np.pi * h)
        # Node lattice coords -> metres (node i sits at voxel i's lower corner).
        pos = np.stack([x[0] - h / 2 + nodes[:, 0] * h, y[0] - h / 2 + nodes[:, 1] * h, z[0] - h / 2 + nodes[:, 2] * h], axis=1)
        zs = 0.06 * H
        a = A_MOUTH + (A_TOP - A_MOUTH) * zs / H
        res = {}
        for name, xs in (("正鼓", 0.0), ("侧鼓", 0.5 * a)):
            # outermost front-face node near (xs, front, zs); front = -y
            near = (np.abs(pos[:, 0] - xs) < 1.5 * h) & (np.abs(pos[:, 2] - zs) < 1.5 * h) & (pos[:, 1] < 0)
            i = np.nonzero(near)[0][np.argmin(pos[near, 1])]
            nrm = np.array([0.0, -1.0, 0.0]) if xs == 0 else np.array([0.35, -0.94, 0.0])
            res[name] = freqs[dominant(phi[i], nrm / np.linalg.norm(nrm), freqs)]
        semis = 12 * np.log2(res["侧鼓"] / res["正鼓"])
        print(f"  depth/width {ratio:.2f}: 正鼓 {res['正鼓']:6.0f} Hz, 侧鼓 {res['侧鼓']:6.0f} Hz -> {semis:+.2f} semitones"
              f"  ({int((occ > 0).sum())} voxels)")


if __name__ == "__main__":
    main()
