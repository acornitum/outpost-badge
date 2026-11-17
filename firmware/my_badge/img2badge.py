#!/usr/bin/env python3
"""Turn a picture (and optional NFC contact details) into the two data files
that the badge's "My Badge" app shows.

    python3 img2badge.py art.png                       # image only
    python3 img2badge.py art.png --nfc url             # + URL from nfc.url.json
    python3 img2badge.py art.png --nfc vcard           # + contact card
    python3 img2badge.py art.png --out /Volumes/CIRCUITPY

Needs Pillow:  pip install pillow

Writes, into --out (default: /Volumes/CIRCUITPY if it is mounted, else ./out):
    badge.bin       4736 bytes, the panel's native 128x296 1-bit buffer
    badge_nfc.bin   CC + NDEF message for the ST25DV tag (only with --nfc)

Then pick "My Badge" in the badge menu. These are data files, so deploying
firmware never touches them.

Image: 296x128 (landscape, how you design it) or 128x296 (panel-native). The
panel is 1-bit: pixels >= --threshold become white, everything else black
(--dither uses error diffusion instead, which suits photos). Transparent areas
count as black. Landscape art is rotated 90 degrees into the native buffer;
that is the right way round for this board with the FPC cable on the left.
If it ever comes out upside down, use --rotate 270.

NFC profiles live next to this script as nfc.url.json / nfc.vcard.json. Copy
the .example.json file and edit it. The real files are git-ignored because they
hold personal details.

Credit: the image-to-bitmap flow and the NDEF URL / vCard builders are adapted
from PR #2 by Tomas Kuchta (Tomas-Kuchta-FPV).
"""
import argparse
import json
import sys
from pathlib import Path

try:
    from PIL import Image
except ImportError:
    sys.exit("error: this tool needs Pillow (pip install pillow)")

HERE = Path(__file__).resolve().parent
PW, PH = 128, 296            # native panel buffer (matches ssd1680.WIDTH / HEIGHT)
NFC_MEM = 512                # ST25DV04K user memory, bytes
URI_PREFIXES = (             # NDEF URI identifier codes
    ("https://www.", 0x02), ("http://www.", 0x01), ("https://", 0x04),
    ("http://", 0x03), ("tel:", 0x05), ("mailto:", 0x06),
)


# ---- image ----------------------------------------------------------------
def to_1bit(im, threshold, invert, dither):
    if im.mode in ("RGBA", "LA", "P") or "transparency" in im.info:   # flatten on black
        im = im.convert("RGBA")
        im = Image.alpha_composite(Image.new("RGBA", im.size, (0, 0, 0, 255)), im)
    g = im.convert("L")
    if invert:
        g = g.point(lambda p: 255 - p)
    if dither:
        return g.convert("1")                        # Floyd-Steinberg
    return g.point(lambda p: 255 if p >= threshold else 0).convert("1")


def panel_buffer(args):
    path = Path(args.image)
    if not path.exists():
        sys.exit(f"error: image not found: {args.image}")
    im = Image.open(path)
    if im.size not in ((PH, PW), (PW, PH)):
        if not args.resize:
            sys.exit(f"error: image is {im.size[0]}x{im.size[1]}; expected {PH}x{PW} "
                     f"(landscape) or {PW}x{PH} (panel-native). Use --resize to scale.")
        im = im.convert("RGBA" if "A" in im.mode else "L").resize((PH, PW), Image.LANCZOS)
    bw = to_1bit(im, args.threshold, args.invert, args.dither)
    if bw.size == (PH, PW):                          # landscape -> native portrait
        bw = bw.rotate(args.rotate, expand=True)
    if args.preview:
        bw.save(args.preview)
    data = bw.tobytes()                              # rows of 16 bytes, MSB first, 1 = white
    assert len(data) == (PW // 8) * PH, len(data)
    return data


# ---- NFC / NDEF -----------------------------------------------------------
def ndef_record(tnf, rtype, payload):
    """Single-record message: MB and ME set; short form when it fits in 255 bytes."""
    if len(payload) <= 255:
        return bytes((0xC0 | 0x10 | tnf, len(rtype), len(payload))) + rtype + payload
    return bytes((0xC0 | tnf, len(rtype))) + len(payload).to_bytes(4, "big") + rtype + payload


def ndef_uri(url):
    for prefix, code in URI_PREFIXES:
        if url.startswith(prefix):
            return ndef_record(0x01, b"U", bytes((code,)) + url[len(prefix):].encode())
    return ndef_record(0x01, b"U", b"\x00" + url.encode())


def vcard_text(c):
    def esc(v):
        v = v.replace("\r\n", "\n").replace("\r", "\n")
        return v.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")
    first, middle, last = c.get("first_name", ""), c.get("middle_name", ""), c.get("last_name", "")
    prefix, suffix = c.get("prefix", ""), c.get("suffix", "")
    display = " ".join(p for p in (prefix, first, middle, last, suffix) if p)
    lines = ["BEGIN:VCARD", "VERSION:3.0",
             f"N:{esc(last)};{esc(first)};{esc(middle)};{esc(prefix)};{esc(suffix)}",
             f"FN:{esc(display)}"]
    for key, field in (("nickname", "NICKNAME"), ("org", "ORG"), ("title", "TITLE"),
                       ("role", "ROLE"), ("birthday", "BDAY"), ("note", "NOTE")):
        if c.get(key):
            lines.append(f"{field}:{esc(c[key])}")
    if c.get("phone"):
        lines.append(f"TEL;TYPE=CELL:{c['phone']}")
    if c.get("phone_work"):
        lines.append(f"TEL;TYPE=WORK,VOICE:{c['phone_work']}")
    if c.get("email"):
        lines.append(f"EMAIL:{c['email']}")
    if c.get("email_work"):
        lines.append(f"EMAIL;TYPE=WORK:{c['email_work']}")
    if c.get("url"):
        lines.append(f"URL:{c['url']}")
    addr = c.get("address", {})
    parts = ("po_box", "extended", "street", "city", "region", "postal_code", "country")
    if any(addr.get(k) for k in parts):
        kind = addr.get("type", "HOME").upper()
        if kind not in ("HOME", "WORK"):
            sys.exit('error: vCard address "type" must be "HOME" or "WORK"')
        lines.append(f"ADR;TYPE={kind}:" + ";".join(esc(addr.get(k, "")) for k in parts))
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def build_nfc(mode):
    """CC + NDEF TLV for the chosen profile, ready to write at address 0."""
    name = {"url": "nfc.url.json", "vcard": "nfc.vcard.json"}[mode]
    f = HERE / name
    if not f.exists():
        sys.exit(f"error: {name} not found; copy {name.replace('.json', '.example.json')} "
                 f"to {name} and edit it")
    cfg = json.loads(f.read_text(encoding="utf-8"))
    if "CHANGE_ME" in json.dumps(cfg):
        sys.exit(f"error: {name} still contains CHANGE_ME placeholders")
    if mode == "url":
        if not cfg.get("url"):
            sys.exit('error: nfc.url.json needs a non-empty "url"')
        msg = ndef_uri(cfg["url"])
    else:
        has_name = any(cfg.get(k) for k in ("first_name", "middle_name", "last_name", "prefix", "suffix"))
        a = cfg.get("address", {})
        has_contact = any(cfg.get(k) for k in ("phone", "phone_work", "email", "email_work", "url", "note")) \
            or any(a.get(k) for k in ("street", "city", "region", "postal_code", "country"))
        if not has_name or not has_contact:
            sys.exit("error: nfc.vcard.json needs a name and at least one contact detail")
        msg = ndef_record(0x02, b"text/vcard", vcard_text(cfg).encode())
    tlv = bytes((0x03, len(msg))) if len(msg) < 255 else b"\x03\xff" + len(msg).to_bytes(2, "big")
    data = bytes((0xE1, 0x40, NFC_MEM // 8, 0x01)) + tlv + msg + b"\xfe"     # CC, TLV, terminator
    if len(data) > NFC_MEM:
        sys.exit(f"error: NFC message is {len(data)} bytes, the tag holds {NFC_MEM}")
    return data


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("image", help="296x128 (landscape) or 128x296 (panel-native) image")
    ap.add_argument("--nfc", choices=("none", "url", "vcard"), default="none",
                    help="NFC payload from nfc.url.json / nfc.vcard.json (default: none)")
    ap.add_argument("--out", help="folder to write into (default: /Volumes/CIRCUITPY, else ./out)")
    ap.add_argument("--rotate", type=int, choices=(90, 270), default=90)
    ap.add_argument("--threshold", type=int, default=128, help="0-255, pixels >= this become white")
    ap.add_argument("--dither", action="store_true", help="error-diffusion instead of a hard threshold")
    ap.add_argument("--invert", action="store_true", help="swap black and white")
    ap.add_argument("--resize", action="store_true", help="scale to 296x128 instead of erroring")
    ap.add_argument("--preview", metavar="PNG", help="save the exact 128x296 buffer as a PNG")
    a = ap.parse_args()

    data = panel_buffer(a)
    nfc = build_nfc(a.nfc) if a.nfc != "none" else None

    out = Path(a.out) if a.out else (Path("/Volumes/CIRCUITPY") if Path("/Volumes/CIRCUITPY").is_dir()
                                      else HERE / "out")
    out.mkdir(parents=True, exist_ok=True)
    (out / "badge.bin").write_bytes(data)
    print(f"wrote {out / 'badge.bin'} ({len(data)} bytes)")
    nfc_path = out / "badge_nfc.bin"
    if nfc is not None:
        nfc_path.write_bytes(nfc)
        print(f"wrote {nfc_path} ({len(nfc)} of {NFC_MEM} bytes, {a.nfc})")
    elif nfc_path.exists():
        nfc_path.unlink()                            # image-only run: stop publishing old NFC data
        print(f"removed old {nfc_path.name} (no --nfc given)")


if __name__ == "__main__":
    main()
