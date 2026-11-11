"""Make textbook-style labelled figures for testing the figure pipeline.

Presets: "met" uses the CC0 Met photo (dark studio backdrop), "white" uses the Lux3D
four-view back render (white ground, like a printed textbook).

Labels sit in the margins with thin leader lines to the part, plus a caption, the
way 历史/音乐 textbooks draw 编钟. Ground truth (label text, leader end point) is
saved next to it so the figure pipeline can be scored.

  python scripts/make_textbook_figure.py
"""
import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "out/inputs/textbook"
FONT = "/System/Library/Fonts/STHeiti Medium.ttc"
# (label, side, label y in photo px, target point in photo px)
PRESETS = {
    "met": (ROOT / "out/inputs/met_bell/DP154556.jpg", "yongzhong_figure", [
        ("甬", "left", 300, (640, 320)),
        ("旋", "left", 640, (585, 655)),
        ("枚", "right", 880, (875, 895)),
        ("钲", "right", 1060, (655, 1060)),
        ("鼓", "left", 1460, (520, 1470)),
    ]),
    "white": (ROOT / "out/lux3d/yongzhong_views/output_3.png", "yongzhong_white_figure", [
        ("甬", "left", 200, (525, 200)),
        ("旋", "left", 345, (472, 345)),
        ("枚", "right", 500, (690, 500)),
        ("钲", "right", 620, (530, 620)),
        ("鼓", "left", 880, (450, 880)),
    ]),
}


def main(preset="met"):
    src, name, labels = PRESETS[preset]
    photo = Image.open(src).convert("RGB")
    W, H = photo.size
    u = H / 1707  # scale type and margins with the photo
    MARGIN, PAD_BOTTOM = int(360 * u), int(160 * u)
    fig = Image.new("RGB", (W + 2 * MARGIN, H + PAD_BOTTOM), "white")
    fig.paste(photo, (MARGIN, 0))
    d = ImageDraw.Draw(fig)
    font = ImageFont.truetype(FONT, max(int(64 * u), 28))
    cap_font = ImageFont.truetype(FONT, max(int(56 * u), 24))
    truth = []
    for text, side, ly, (tx, ty) in labels:
        tx, ty = tx + MARGIN, ty
        lx = MARGIN - int(210 * u) if side == "left" else MARGIN + W + int(140 * u)
        d.text((lx, ly - int(36 * u)), text, font=font, fill="black")
        start = (lx + int(80 * u), ly) if side == "left" else (lx - int(16 * u), ly)
        d.line([start, (tx, ty)], fill="black", width=max(int(4 * u), 2))
        r = max(int(7 * u), 4)
        d.ellipse([tx - r, ty - r, tx + r, ty + r], fill="black")
        truth.append({"label": text, "target": [tx, ty]})
    caption = "图1  甬钟"
    cw = d.textlength(caption, font=cap_font)
    d.text(((fig.width - cw) / 2, H + int(50 * u)), caption, font=cap_font, fill="black")
    OUT.mkdir(parents=True, exist_ok=True)
    fig.save(OUT / f"{name}.png")
    (OUT / f"{name}_truth.json").write_text(json.dumps(
        {"caption": caption, "labels": truth, "photo_offset": [MARGIN, 0], "photo_size": [W, H]},
        ensure_ascii=False, indent=1))
    print(f"wrote {OUT / (name + '.png')} ({fig.width}x{fig.height})")


if __name__ == "__main__":
    import sys
    main(sys.argv[1] if len(sys.argv) > 1 else "met")
