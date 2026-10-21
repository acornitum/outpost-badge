#!/usr/bin/env python3
"""Turn badge.png into a CircuitPython code.py for the outpost badge.

Run on your computer (needs Pillow: pip install pillow):

    python3 img2code.py                # badge.png -> code.py, both next to this script
    python3 img2code.py other.png      # use a different image

badge.png and code_template.py are read from the folder this script lives in,
and code.py is always written there too. Copy code.py to the CIRCUITPY drive.

The e-ink panel is 1-bit (no greys): pixels >= --threshold become white,
everything else black. Transparent areas count as black.

Input: 296x128 (landscape, how you design it) or 128x296 (panel-native).
Landscape images are rotated into the panel's native 128x296 buffer; if the
result is upside down on the badge, re-run with --rotate 270.
"""
import argparse
import sys
from pathlib import Path

from PIL import Image

HERE = Path(__file__).resolve().parent
PW, PH = 128, 296  # native panel buffer (matches ssd1680.WIDTH / HEIGHT)


def to_1bit(im, threshold, invert):
    if im.mode in ("RGBA", "LA", "P") or "transparency" in im.info:  # flatten onto black
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (0, 0, 0, 255)), im)
    g = im.convert("L")
    if invert:
        g = g.point(lambda p: 255 - p)
    return g.point(lambda p: 255 if p >= threshold else 0).convert("1")


def pack(bw):
    """Row-major, MSB first, rows padded to bytes. 1 = white."""
    w, h = bw.size
    px = bw.load()
    stride = (w + 7) // 8
    out = bytearray()
    for y in range(h):
        row = bytearray(stride)
        for x in range(w):
            if px[x, y]:
                row[x >> 3] |= 0x80 >> (x & 7)
        out += row
    return bytes(out)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", nargs="?", default="badge.png",
                    help="input image (default: badge.png next to this script)")
    ap.add_argument("--rotate", type=int, choices=(90, 270), default=90,
                    help="how landscape 296x128 input is turned into the 128x296 panel buffer (default 90)")
    ap.add_argument("--threshold", type=int, default=128, help="0-255, pixels >= this become white")
    ap.add_argument("--invert", action="store_true", help="swap black and white")
    ap.add_argument("--resize", action="store_true", help="scale to 296x128 instead of erroring")
    ap.add_argument("--preview", metavar="PNG", help="save the exact 128x296 buffer as a PNG")
    a = ap.parse_args()

    img_path = Path(a.image)
    if not img_path.is_absolute() and not img_path.exists():
        img_path = HERE / a.image
    if not img_path.exists():
        sys.exit(f"error: image not found: {a.image}")

    im = Image.open(img_path)
    if im.size not in ((PH, PW), (PW, PH)):
        if not a.resize:
            sys.exit(f"error: image is {im.size[0]}x{im.size[1]}; expected {PH}x{PW} (landscape) "
                     f"or {PW}x{PH} (panel-native). Use --resize to scale to {PH}x{PW}.")
        im = im.convert("RGBA" if "A" in im.mode else "L").resize((PH, PW), Image.LANCZOS)

    bw = to_1bit(im, a.threshold, a.invert)
    if bw.size == (PH, PW):  # landscape -> native portrait
        bw = bw.rotate(a.rotate, expand=True)
    data = pack(bw)
    assert len(data) == (PW // 8) * PH
    if a.preview:
        bw.save(a.preview)

    hexs = data.hex()
    lines = "\n".join(f'    "{hexs[i:i+64]}"' for i in range(0, len(hexs), 64))
    code = ((HERE / "code_template.py").read_text()
            .replace("{{SOURCE}}", img_path.name)
            .replace("{{WIDTH}}", str(PW))
            .replace("{{HEIGHT}}", str(PH))
            .replace("{{DATA}}", lines))
    out = HERE / "code.py"
    out.write_text(code)
    print(f"wrote {out}: panel buffer {PW}x{PH}, {len(data)} bytes")


if __name__ == "__main__":
    main()
