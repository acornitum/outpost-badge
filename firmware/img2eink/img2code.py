#!/usr/bin/env python3
"""Turn badge.png into a CircuitPython code.py for the outpost badge.

Run on your computer (needs Pillow: pip install pillow):

    python3 img2code.py                # badge.png -> code.py, both next to this script
    python3 img2code.py other.png      # use a different image

badge.png and code_template.py are read from the folder this script lives in,
and code.py is always written there too. Copy code.py to the CIRCUITPY drive.

The e-ink panel is 1-bit (no greys): pixels >= --threshold become white,
everything else black. Transparent areas count as black.

NFC: without --publish, the built-in project URL is used and RF writes remain
open. --publish url and --publish vcard use their matching JSON profiles and
enable boot-time RF write protection (hold GP4 or GP5 low to unlock).

Input: 296x128 (landscape, how you design it) or 128x296 (panel-native).
Landscape images are rotated into the panel's native 128x296 buffer; if the
result is upside down on the badge, re-run with --rotate 270.
"""
import argparse
import json
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


# ---- NFC / NDEF -----------------------------------------------------------
NFC_MEM = 512  # ST25DV04K user memory, bytes
DEFAULT_URL = "https://github.com/acornitum/outpost-badge"
URI_PREFIXES = (  # NDEF URI identifier codes
    ("https://www.", 0x02), ("http://www.", 0x01), ("https://", 0x04), ("http://", 0x03),
    ("tel:", 0x05), ("mailto:", 0x06),
)


def ndef_record(tnf, rtype, payload):
    """Single-record message: MB and ME set, short form when the payload fits in 255 bytes."""
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
    first = c.get("first_name", "")
    middle = c.get("middle_name", "")
    last = c.get("last_name", "")
    prefix = c.get("prefix", "")
    suffix = c.get("suffix", "")
    display_name = " ".join(part for part in (prefix, first, middle, last, suffix) if part)
    lines = ["BEGIN:VCARD", "VERSION:3.0",
             f"N:{esc(last)};{esc(first)};{esc(middle)};{esc(prefix)};{esc(suffix)}",
             f"FN:{esc(display_name)}"]
    if c.get("nickname"):
        lines.append(f"NICKNAME:{esc(c['nickname'])}")
    if c.get("org"):
        lines.append(f"ORG:{esc(c['org'])}")
    if c.get("title"):
        lines.append(f"TITLE:{esc(c['title'])}")
    if c.get("role"):
        lines.append(f"ROLE:{esc(c['role'])}")
    if c.get("birthday"):
        lines.append(f"BDAY:{esc(c['birthday'])}")
    if c.get("note"):
        lines.append(f"NOTE:{esc(c['note'])}")
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
    address = c.get("address", {})
    if address and any(address.get(key) for key in ("po_box", "extended", "street", "city", "region", "postal_code", "country")):
        address_type = address.get("type", "HOME").upper()
        if address_type not in ("HOME", "WORK"):
            sys.exit('error: vCard address "type" must be "HOME" or "WORK"')
        parts = ("po_box", "extended", "street", "city", "region", "postal_code", "country")
        lines.append(f"ADR;TYPE={address_type}:" + ";".join(esc(address.get(key, "")) for key in parts))
    lines.append("END:VCARD")
    return "\r\n".join(lines) + "\r\n"


def build_nfc(cfg, mode):
    """Build a Type 5 NDEF TLV from the selected URL or vCard profile."""
    if "CHANGE_ME" in json.dumps(cfg):
        sys.exit("error: selected NFC profile still contains CHANGE_ME placeholders")
    if mode == "url":
        if not cfg.get("url"):
            sys.exit('error: nfc.url.json needs a non-empty "url"')
        msg = ndef_uri(cfg["url"])
    elif mode == "vcard":
        v = cfg
        has_name = any(v.get(key) for key in ("first_name", "middle_name", "last_name", "prefix", "suffix"))
        has_contact = any(v.get(key) for key in ("phone", "phone_work", "email", "email_work", "url", "note"))
        address = v.get("address", {})
        has_contact = has_contact or any(address.get(key) for key in ("street", "city", "region", "postal_code", "country"))
        if not has_name or not has_contact:
            sys.exit("error: nfc.vcard.json needs a name and at least one contact detail")
        msg = ndef_record(0x02, b"text/vcard", vcard_text(v).encode())
    else:
        sys.exit('error: NFC profile must be "url" or "vcard"')
    tlv = bytes((0x03, len(msg))) if len(msg) < 255 else b"\x03\xff" + len(msg).to_bytes(2, "big")
    data = bytes((0xE1, 0x40, NFC_MEM // 8, 0x01)) + tlv + msg + b"\xfe"  # CC, TLV, terminator
    if len(data) > NFC_MEM:
        sys.exit(f"error: NFC message is {len(data)} bytes, the tag holds {NFC_MEM}")
    return data, mode


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
    ap.add_argument("--publish", choices=("url", "vcard"),
                    help="use the matching JSON profile with boot-time write protection (default: built-in URL, unprotected)")
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

    nfc_src = "None"
    if a.publish is None:
        nfc, mode = build_nfc({"url": DEFAULT_URL}, "url")
        print(f"NFC: publishing built-in URL, RF writes open ({len(nfc)} of {NFC_MEM} bytes)")
    else:
        mode = a.publish
        nfc_file = HERE / ("nfc.vcard.json" if mode == "vcard" else "nfc.url.json")
        if not nfc_file.exists():
            example = "nfc.vcard.example.json" if mode == "vcard" else "nfc.url.example.json"
            sys.exit(f"error: {nfc_file.name} not found; copy {example} to {nfc_file.name} and edit it")
        nfc, mode = build_nfc(json.loads(nfc_file.read_text(encoding="utf-8")), mode)
        print(f"NFC: publishing {mode} profile with boot-time RF write protection ({len(nfc)} of {NFC_MEM} bytes)")

    nh = nfc.hex()
    nfc_src = "bytes.fromhex(\n" + "\n".join(f'    "{nh[i:i+64]}"' for i in range(0, len(nh), 64)) + "\n)"

    hexs = data.hex()
    lines = "\n".join(f'    "{hexs[i:i+64]}"' for i in range(0, len(hexs), 64))
    code = ((HERE / "code_template.py").read_text()
            .replace("{{SOURCE}}", img_path.name)
            .replace("{{WIDTH}}", str(PW))
            .replace("{{HEIGHT}}", str(PH))
            .replace("{{NFC_DATA}}", nfc_src)
            .replace("{{NFC_MODE}}", mode)
            .replace("{{NFC_UNLOCK_REQUIRED}}", str(a.publish is not None))
            .replace("{{DATA}}", lines))
    out = HERE / "code.py"
    out.write_text(code)
    print(f"wrote {out}: panel buffer {PW}x{PH}, {len(data)} bytes")


if __name__ == "__main__":
    main()
