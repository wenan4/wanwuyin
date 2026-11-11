"""Check the voxel FEM against closed-form free-free steel bar frequencies.

A 300 x 20 x 20 mm steel bar is voxelised exactly (no mesh step) so only the
element formulation is under test.
"""
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys.fem import NU, solve_modes  # noqa: E402

E, RHO = 200e9, 7850.0
L, A_SIDE = 0.30, 0.02


def analytic():
    c = np.sqrt(E / RHO)
    bend = A_SIDE * np.sqrt(E / (12 * RHO))  # sqrt(EI / rho A) for a square section
    G = E / (2 * (1 + NU))
    return {
        "bending-1": 4.7300 ** 2 / (2 * np.pi * L ** 2) * bend,
        "bending-2": 7.8532 ** 2 / (2 * np.pi * L ** 2) * bend,
        "torsion-1": np.sqrt(G / RHO * 0.1406 * 6) / (2 * L),
        "axial-1": c / (2 * L),
    }


def classify(phi, node_xyz):
    """Label each mode as axial / torsion / bending from its displacement field."""
    yz = node_xyz[:, 1:] - node_xyz[:, 1:].mean(axis=0)
    labels = []
    for i in range(phi.shape[2]):
        u = phi[:, :, i]
        total = (u ** 2).sum()
        axial = (u[:, 0] ** 2).sum() / total
        # Pointwise: the twist angle changes sign along the bar, so a global sum cancels.
        twist = ((yz[:, 0] * u[:, 2] - yz[:, 1] * u[:, 1]) ** 2).sum()
        twist /= ((yz ** 2).sum(axis=1) * (u[:, 1:] ** 2).sum(axis=1)).sum() + 1e-30
        labels.append("axial" if axial > 0.8 else "torsion" if twist > 0.8 else "bending")
    return labels


def main(n_long=120):
    n_side = round(n_long * A_SIDE / L)
    occ = np.ones((n_long, n_side, n_side), bool)
    t0 = time.time()
    lam, phi, node_xyz = solve_modes(occ, n_modes=12)
    h = L / n_long
    freqs = np.sqrt(lam * E / RHO) / (2 * np.pi * h)
    print(f"grid {occ.shape}, {occ.sum()} voxels, solve {time.time() - t0:.1f}s")

    labels = classify(phi, node_xyz)
    found = {}
    for f, lab in zip(freqs, labels):
        key = f"{lab}-{sum(k.startswith(lab) for k in found) + 1}"
        # Degenerate bending pairs (y and z) share one analytic value.
        if lab == "bending" and any(abs(f - v) / v < 0.01 for k, v in found.items() if k.startswith("bending")):
            continue
        found[key] = f

    print(f"{'mode':<11}{'analytic Hz':>12}{'FEM Hz':>10}{'error':>9}")
    worst = 0.0
    for key, ref in analytic().items():
        got = found.get(key)
        err = (got - ref) / ref if got else float("nan")
        worst = max(worst, abs(err))
        print(f"{key:<11}{ref:>12.0f}{got or float('nan'):>10.0f}{err:>+9.1%}")
    return worst


if __name__ == "__main__":
    for n in (60, 120):
        print(f"\n--- {n} voxels along the bar ---")
        main(n)
