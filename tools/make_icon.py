# -*- coding: utf-8 -*-
"""Draw the CBoE app icon (the review page's favicon: lavender tile, three subtitle lines).

    .venv\\Scripts\\python.exe tools\\make_icon.py      -> cboe/server/static/cboe.ico
"""
from pathlib import Path

from PIL import Image, ImageDraw

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "cboe" / "server" / "static" / "cboe.ico"


def draw(size: int = 1024) -> Image.Image:
    k = size / 32                                    # the favicon is drawn on a 32 x 32 grid
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, size - 1, size - 1], radius=round(8 * k), fill=(0x71, 0x80, 0xCB, 255))
    w = round(2.4 * k)
    for y, x1 in ((12, 24), (17, 19), (22, 22)):
        a, b, yy = 8 * k, x1 * k, y * k
        d.line([(a, yy), (b, yy)], fill="white", width=w)
        for x in (a, b):                              # round caps
            d.ellipse([x - w / 2, yy - w / 2, x + w / 2, yy + w / 2], fill="white")
    return img


if __name__ == "__main__":
    big = draw()
    sizes = [16, 20, 24, 32, 40, 48, 64, 128, 256]
    big.resize((256, 256), Image.LANCZOS).save(OUT, sizes=[(s, s) for s in sizes])
    print(OUT)
