"""Draw the app icon (cells in a stream crossing a laser line, on a blue tile, as in flowbench/web/icon.svg)
and write AppIcon.icns.   python make_icon.py OUT.icns   |   python make_icon.py --png OUT.png"""
import os
import subprocess
import sys
import tempfile

from PIL import Image, ImageDraw


def draw(size: int) -> Image.Image:
    s = size * 4                      # supersample, then shrink
    u = s / 64                        # the SVG is drawn on a 64-unit grid
    img = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    tile = Image.new("RGBA", (s, s))
    td = ImageDraw.Draw(tile)
    a, b = (43, 155, 255), (10, 79, 196)
    for i in range(2 * s):            # diagonal gradient, top left to bottom right
        t = i / (2 * s)
        c = tuple(int(a[k] + (b[k] - a[k]) * t) for k in range(3)) + (255,)
        td.line((i, 0, 0, i), fill=c, width=2)
    mask = Image.new("L", (s, s), 0)
    ImageDraw.Draw(mask).rounded_rectangle((3 * u, 3 * u, 61 * u, 61 * u), radius=int(13.5 * u), fill=255)
    img.paste(tile, (0, 0), mask)
    over = Image.new("RGBA", (s, s), (0, 0, 0, 0))
    od = ImageDraw.Draw(over)

    def line(x0, y0, x1, y1, col, w):
        od.line((x0 * u, y0 * u, x1 * u, y1 * u), fill=col, width=int(w * u))
        for x, y in ((x0, y0), (x1, y1)):
            od.ellipse((x * u - w * u / 2, y * u - w * u / 2, x * u + w * u / 2, y * u + w * u / 2), fill=col)

    line(9, 32, 55, 32, (255, 255, 255, 115), 3)
    line(40, 12, 40, 52, (255, 209, 102, 255), 3.4)
    line(46, 25, 52, 19, (255, 209, 102, 255), 2.6)
    line(46, 39, 52, 45, (255, 209, 102, 255), 2.6)
    for x, r in ((17, 4), (28.5, 4), (40, 6.6)):
        od.ellipse(((x - r) * u, (32 - r) * u, (x + r) * u, (32 + r) * u), fill=(255, 255, 255, 255))
    img = Image.alpha_composite(img, over)
    return img.resize((size, size), Image.LANCZOS)


def main() -> None:
    if len(sys.argv) == 3 and sys.argv[1] == "--png":
        draw(512).save(sys.argv[2])
        return
    out = sys.argv[1]
    with tempfile.TemporaryDirectory() as tmp:
        iconset = os.path.join(tmp, "AppIcon.iconset")
        os.mkdir(iconset)
        for base in (16, 32, 128, 256, 512):
            draw(base).save(os.path.join(iconset, f"icon_{base}x{base}.png"))
            draw(base * 2).save(os.path.join(iconset, f"icon_{base}x{base}@2x.png"))
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", out], check=True)


if __name__ == "__main__":
    main()
