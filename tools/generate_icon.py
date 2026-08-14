# -*- coding: utf-8 -*-
"""Generate the Windows ICO used by InvoiceManager releases."""

from pathlib import Path
from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
ASSETS = ROOT / "assets"
OUT = ASSETS / "InvoiceManager.ico"
PNG = ASSETS / "InvoiceManager.png"

SIZES = [16, 24, 32, 48, 64, 128, 256]


def rounded_rect(draw, box, radius, fill):
    draw.rounded_rectangle(box, radius=radius, fill=fill)


def make_icon(size: int) -> Image.Image:
    scale = size / 512
    im = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(im)

    # App tile.
    rounded_rect(d, (int(32*scale), int(32*scale), int(480*scale), int(480*scale)), int(112*scale), (37, 99, 235, 255))

    # Invoice/document body.
    body = [int(v * scale) for v in (144, 100, 368, 412)]
    d.rounded_rectangle(body, radius=max(1, int(18*scale)), fill=(255, 255, 255, 250))

    # Folded corner.
    fold = [(int(292*scale), int(100*scale)), (int(368*scale), int(176*scale)), (int(292*scale), int(176*scale))]
    d.polygon(fold, fill=(219, 234, 254, 255))

    # Text lines.
    line_w = max(1, int(18*scale))
    for y, x2 in ((228, 318), (272, 318), (316, 272)):
        d.line((int(194*scale), int(y*scale), int(x2*scale), int(y*scale)), fill=(147, 197, 253, 255), width=line_w)

    # Check mark.
    check_w = max(2, int(26*scale))
    pts = [(int(236*scale), int(356*scale)), (int(270*scale), int(390*scale)), (int(344*scale), int(304*scale))]
    d.line(pts, fill=(15, 118, 110, 255), width=check_w, joint="curve")

    return im


def main():
    ASSETS.mkdir(parents=True, exist_ok=True)
    images = [make_icon(s) for s in SIZES]
    images[-1].save(PNG)
    images[-1].save(OUT, format="ICO", sizes=[(s, s) for s in SIZES])
    print(f"Generated {OUT}")
    print(f"Generated {PNG}")


if __name__ == "__main__":
    main()
