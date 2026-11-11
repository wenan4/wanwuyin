"""Fast checks for the pure-logic pieces (no Lux3D calls, no big solves).

  python scripts/selftest.py
"""
import shutil
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
sys.path.insert(0, str(Path(__file__).resolve().parent))
from wwys import fem, lux3d, materials, synth, tactile  # noqa: E402


def check(name, ok):
    print(f"{'ok  ' if ok else 'FAIL'} {name}")
    return ok


def main():
    results = []
    # OUS block upload: lackBlocks parsing ("" = all, ranges, lists)
    results.append(check("lackBlocks all", lux3d._lack_blocks("", 3) == [1, 2, 3]))
    results.append(check("lackBlocks ranges", lux3d._lack_blocks("1,3-4", 5) == [1, 3, 4]))
    results.append(check("lackBlocks list + bounds", lux3d._lack_blocks([2, 9], 3) == [2]))

    # Part spec parsing for tactile numbering
    import make_tactile
    results.append(check("parts spec", make_tactile.parse_parts("1-2=甬;3=枚", 4) == [("甬", [0, 1]), ("枚", [2])]))

    # Scaling laws the whole product leans on: f ∝ 1/size and ∝ sqrt(E/rho)
    m = fem.ModalModel(np.array([1e-4, 4e-4]), np.zeros((1, 3, 2)), np.zeros((1, 3)), 1 / 64, np.zeros(3), 1)
    p = materials.params("steel")
    f1, f2 = m.frequencies(p["E"], p["rho"], 0.1), m.frequencies(p["E"], p["rho"], 0.2)
    results.append(check("f ∝ 1/size", np.allclose(f1 / f2, 2)))
    q = materials.params("wood")
    fw = m.frequencies(q["E"], q["rho"], 0.1)
    results.append(check("f ∝ sqrt(E/rho)", np.allclose(f1 / fw, np.sqrt((p["E"] / p["rho"]) / (q["E"] / q["rho"])))))

    # Thin-wall correction table is monotone and vanishes for thick walls
    vals = [fem.shell_overstiffness(n) for n in (2.0, 2.4, 3.1, 4.5)]
    results.append(check("shell correction decreasing", all(a >= b for a, b in zip(vals, vals[1:])) and vals[-1] == 0))

    # Hammer spectrum: soft mallets cut highs
    f = np.array([100.0, 4000.0])
    results.append(check("contact spectrum", synth.contact_spectrum(f, 2e-3)[1] < synth.contact_spectrum(f, 1e-4)[1]))

    # Element: symmetric, exactly six rigid-body zero modes
    w = np.linalg.eigvalsh(fem.element_stiffness())
    results.append(check("hex element rigid modes", int((abs(w) < 1e-10).sum()) == 6))

    # Braille layout (needs liblouis)
    if shutil.which("lou_translate"):
        cells = tactile.to_braille("碗")
        dots = tactile.dot_centres([cells], tactile.BrailleSpec(), 0, 0)
        expect = sum(bin(ord(c) - 0x2800).count("1") for c in cells)
        results.append(check("braille dot count", len(dots) == expect))
        results.append(check("braille wrap", all(len(ln) <= 6 for ln in tactile.wrap_cells(tactile.to_braille("实物口径约十五厘米"), 6))))
    else:
        print("skip braille (no liblouis)")

    bad = results.count(False)
    print(f"\n{len(results) - bad}/{len(results)} passed")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
