"""sim2real step 1: calibrate the effective stiffness of 3D-printed PLA.

  python scripts/calibrate_print.py make  --out out/calibration          # STL + instructions
  python scripts/calibrate_print.py tap   out/calibration/tap.wav        # after recording

A free-free bar has a closed-form first bending mode, so one recording pins down
E_eff = E_ref * (f_measured / f_predicted)^2 for this printer/filament/settings.
"""
import argparse
import sys
from pathlib import Path

import numpy as np
import trimesh
from scipy.io import wavfile
from scipy.signal import find_peaks

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from wwys import materials  # noqa: E402

L, W, T = 0.160, 0.020, 0.008  # metres
BETA1 = 4.7300  # free-free first bending eigenvalue
NODE = 0.224    # node positions as a fraction of length from each end


def predicted_f1(E, rho):
    return BETA1 ** 2 / (2 * np.pi * L ** 2) * T * np.sqrt(E / (12 * rho))


def make(out):
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    trimesh.creation.box(extents=(L * 1000, W * 1000, T * 1000)).export(out / "calibration_bar.stl")
    rho = materials.params("pla")["rho"]
    lines = [
        "PLA 校准条（160 × 20 × 8 mm）",
        "",
        "打印：100% 填充，平放打印（8 mm 方向朝上），线条方向沿长边，记录打印机/耗材/层高。",
        f"悬挂：在距两端各 {NODE * L * 1000:.0f} mm 处用细线或橡皮筋吊起（第一阶弯曲的节点，阻尼最小）。",
        "敲击：用硬物（钥匙、螺丝刀柄）轻敲正中央，手机离 10–20 cm 录音，安静环境，录 3–5 下。",
        "",
        "预测第一阶频率（密度 1240 kg/m³）：",
        *[f"  E = {E / 1e9:.1f} GPa -> {predicted_f1(E, rho):.0f} Hz" for E in (2.5e9, 3.0e9, 3.5e9)],
        "",
        "录完后：python scripts/calibrate_print.py tap <录音.wav>",
    ]
    (out / "README.txt").write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines))


def tap(wav, band=(150, 2000)):
    sr, y = wavfile.read(wav)
    y = y.astype(float)
    if y.ndim > 1:
        y = y.mean(axis=1)
    y /= np.abs(y).max() + 1e-12
    hits, _ = find_peaks(np.abs(y), height=0.5, distance=int(0.3 * sr))
    if not len(hits):
        sys.exit("no taps found (peaks above half of the maximum)")
    rho, E_ref = materials.params("pla")["rho"], materials.params("pla")["E"]
    f_pred = predicted_f1(E_ref, rho)
    found = []
    for h in hits:
        seg = y[h + int(0.005 * sr): h + int(0.25 * sr)]  # skip the click, keep the ring
        if len(seg) < sr // 20:
            continue
        spec = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), n=1 << 16))
        fr = np.fft.rfftfreq(1 << 16, 1 / sr)
        m = (fr > band[0]) & (fr < band[1])
        found.append(fr[m][np.argmax(spec[m])])
    f = float(np.median(found))
    E_eff = E_ref * (f / f_pred) ** 2
    print(f"{len(found)} taps, first-mode peak {f:.0f} Hz (spread {np.ptp(found):.0f} Hz)")
    print(f"predicted at E={E_ref / 1e9:.1f} GPa: {f_pred:.0f} Hz -> effective E = {E_eff / 1e9:.2f} GPa")
    print("把这个 E 写进 wwys/materials.py 的 \"pla\" 一行，再用它预测打印件的声音。")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    m = sub.add_parser("make")
    m.add_argument("--out", default="out/calibration")
    t = sub.add_parser("tap")
    t.add_argument("wav")
    args = ap.parse_args()
    make(args.out) if args.cmd == "make" else tap(args.wav)


if __name__ == "__main__":
    main()
