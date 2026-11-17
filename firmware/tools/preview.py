#!/usr/bin/env python3
"""Render previews of the badge apps on your computer, no badge needed.

    python3 firmware/tools/preview.py                  # every scenario -> ./preview/
    python3 firmware/tools/preview.py --only snake pong
    python3 firmware/tools/preview.py --image art.png  # My Badge with your picture
    python3 firmware/tools/preview.py --list

Needs Pillow (pip install pillow). Writes one PNG and one GIF per scenario,
plus contact_sheet.png with a representative frame of each.

How it works: it does not reimplement anything. It runs the real scripts
(menu.py, snake.py, pong.py, flappy.py, nfc_text.py, ...) against a fake
CircuitPython world:

  * a fake SSD1680 that records every buffer the app sends to the panel;
  * fake buttons driven by a timeline of presses;
  * a virtual clock, so a 2.3 s refresh costs nothing to wait for;
  * a fake ST25DV tag that a pretend phone writes to at set times;
  * the badge's own 6x12 font (font6x12.json, dumped from a board).

Frames are turned into pictures with the same screen<->panel mapping the
firmware uses (screen x -> panel row 295-x, y -> column).

What this is NOT: it shows ideal pixels. Ghosting, the contrast cost of the fast
waveform, and the real refresh timing are not modelled, and the timings in the
GIFs are approximate. Treat it as "what is drawn", not "how it looks on e-ink".
"""
import argparse
import builtins
import contextlib
import io
import json
import os
import random
import sys
import types
from pathlib import Path

try:
    from PIL import Image, ImageDraw, ImageFont, ImageOps
except ImportError:
    sys.exit("error: this tool needs Pillow (pip install pillow)")

HERE = Path(__file__).resolve().parent
FW = HERE.parent
W, H = 296, 128
PAPER = (222, 220, 208)
INK = (24, 24, 26)
BUTTONS = {7: "up", 8: "left", 6: "right", 9: "down", 5: "a", 4: "b"}   # GPIO -> name
UID = bytes.fromhex("e002256680027c33")

sys.path.insert(0, str(FW / "lib"))
import st25dv as _st                                      # noqa: E402  (builds NDEF messages)
sys.path.insert(0, str(FW / "my_badge"))
import img2badge as _ib                                   # noqa: E402  (picture -> panel bytes)
for _m in ("st25dv",):
    sys.modules.pop(_m, None)                             # apps re-import it against the fakes

FONT = json.loads((HERE / "font6x12.json").read_text())


class SimStop(BaseException):
    """supervisor.reload() was called."""


class World:
    def __init__(self, limit, presses=(), phone=(), files=None):
        self.t = 0.0
        self.limit = limit
        self.stopped = False
        self.presses = list(presses)           # (down_t, up_t, button)
        self.phone = sorted(phone)             # (t, ndef message bytes)
        self.files = dict(files or {})
        self.frames = []                       # (t, bytes)
        self.mem = bytearray(0x2010)           # user memory + dynamic registers
        self.sysmem = bytearray(0x1000)
        self.sysmem[0x14:0x17] = bytes((0x7F, 0x00, 0x03))      # 128 blocks x 4 B = 512 B
        self.sysmem[0x17] = 0x24
        self.sysmem[0x18:0x20] = UID[::-1]
        self.launched = None

    def tick(self, dt):
        self.t += dt
        if self.t > self.limit and not self.stopped:
            self.stopped = True
            raise KeyboardInterrupt            # the same exit the A+B combo uses

    def pressed(self, name):
        return any(d <= self.t < u and n == name for d, u, n in self.presses)

    def apply_phone(self):
        while self.phone and self.phone[0][0] <= self.t:
            msg = self.phone.pop(0)[1]
            data = bytes((0xE1, 0x40, 0x40, 0x00, 0x03, len(msg))) + msg + b"\xfe"
            self.mem[0:len(data)] = data

    def capture(self, buf):
        if not self.stopped:                   # skip the shutdown clean-up refresh
            self.frames.append((self.t, bytes(buf)))


def build_modules(w):
    mods = {}

    tm = types.ModuleType("time")
    tm.monotonic = lambda: (w.tick(1e-4), w.t)[1]        # time passes while code runs
    tm.sleep = lambda s: w.tick(s)
    tm.time = lambda: w.t
    mods["time"] = tm

    class Pin:
        def __init__(self, n):
            self.n = n
    board = types.ModuleType("board")
    for n in range(30):
        setattr(board, "GP%d" % n, Pin(n))
    mods["board"] = board

    class Direction:
        INPUT, OUTPUT = "in", "out"

    class Pull:
        UP, DOWN = "up", "down"

    class DigitalInOut:
        def __init__(self, pin):
            self.pin, self.direction, self.pull, self._v = pin, None, None, True

        def switch_to_input(self, pull=None):
            self.pull = pull

        @property
        def value(self):
            if self.pin.n in BUTTONS:                    # switch to ground: pressed = low
                return not w.pressed(BUTTONS[self.pin.n])
            return self._v

        @value.setter
        def value(self, v):
            self._v = v

        def deinit(self):
            pass
    dio = types.ModuleType("digitalio")
    dio.Direction, dio.Pull, dio.DigitalInOut = Direction, Pull, DigitalInOut
    mods["digitalio"] = dio

    class SPI:
        def __init__(self, *a, **k):
            pass

    class I2C:
        def __init__(self, *a, **k):
            pass

        def try_lock(self):
            return True

        def unlock(self):
            pass

        def deinit(self):
            pass

        def scan(self):
            return [0x2D, 0x53, 0x57]

        def _space(self, dev):
            w.apply_phone()
            if dev == 0x53:
                return w.mem
            if dev == 0x57:
                return w.sysmem
            raise OSError(19)

        def writeto(self, dev, data):
            space = self._space(dev)
            if len(data) > 2 and dev == 0x53:
                ptr = (data[0] << 8) | data[1]
                if ptr < 512:
                    space[ptr:ptr + len(data) - 2] = data[2:]

        def writeto_then_readfrom(self, dev, out, buf):
            space = self._space(dev)
            ptr = (out[0] << 8) | out[1]
            buf[:] = space[ptr:ptr + len(buf)]
    bus = types.ModuleType("busio")
    bus.SPI, bus.I2C = SPI, I2C
    mods["busio"] = bus

    sup = types.ModuleType("supervisor")

    def set_next(path, **k):
        w.launched = path

    def reload():
        raise SimStop
    sup.set_next_code_file, sup.reload = set_next, reload
    mods["supervisor"] = sup

    ssd = types.ModuleType("ssd1680")
    ssd.WIDTH, ssd.HEIGHT = 128, 296

    class SSD1680:
        def __init__(self, *a, **k):
            self.timeout = 3.0

        def init(self):
            return True

        def display_full(self, buf):
            w.capture(buf)
            w.tick(2.29)

        display_base = display_full

        def display_partial(self, buf, fast=True):
            w.capture(buf)
            w.tick(0.617)

        def arm_partial(self, *a, **k):
            w.tick(0.14)

        def frame_nopower(self, buf, mode=0x0C, window=None, sync_old=True, poll=None):
            w.capture(buf)
            for _ in range(10):                          # the ~107 ms BUSY wait, polled
                w.tick(0.0107)
                if poll:
                    poll()
            return (0.0, 0.1, True)

        def power_off(self):
            w.tick(0.01)

        def power_on(self):
            pass

        def sleep(self):
            pass
    ssd.SSD1680 = SSD1680
    mods["ssd1680"] = ssd

    tiles = [FONT["glyphs"][str(c)] for c in range(32, 127)]

    class Bitmap:
        def __getitem__(self, xy):
            x, y = xy
            return (tiles[x // 6][y] >> (5 - x % 6)) & 1

    class Glyph:
        def __init__(self, cp):
            self.bitmap, self.tile_index = Bitmap(), cp - 32

    class BuiltinFont:
        def get_glyph(self, cp):
            return Glyph(cp) if 32 <= cp <= 126 else None

        def get_bounding_box(self):
            return (6, 12)
    term = types.ModuleType("terminalio")
    term.FONT = BuiltinFont()
    mods["terminalio"] = term
    return mods


FIRMWARE_MODULES = ("badge", "eink_text", "gfx", "st25dv")


@contextlib.contextmanager
def fake_world(w):
    mods = build_modules(w)
    saved = {k: sys.modules.get(k) for k in list(mods) + list(FIRMWARE_MODULES)}
    real_open, real_listdir = builtins.open, os.listdir

    def fake_open(path, mode="r", *a, **k):
        if path in w.files:
            return io.BytesIO(w.files[path])
        return real_open(path, mode, *a, **k)

    def fake_listdir(path="."):
        if path == "/apps":
            apps = (FW / "menu" / "apps.txt").read_text().split("\n")
            return [Path(a).name for a in apps if a and not a.startswith("#")]
        return real_listdir(path)
    for name in FIRMWARE_MODULES:
        sys.modules.pop(name, None)
    sys.modules.update(mods)
    builtins.open, os.listdir = fake_open, fake_listdir
    try:
        yield
    finally:
        builtins.open, os.listdir = real_open, real_listdir
        for k, v in saved.items():
            if v is None:
                sys.modules.pop(k, None)
            else:
                sys.modules[k] = v


def run(sc, files=None):
    world = World(sc["limit"], sc.get("presses", ()), sc.get("phone", ()), files)
    src = (FW / sc["script"]).read_text()
    for a, b in sc.get("patch", ()):
        assert a in src, a
        src = src.replace(a, b)
    random.seed(1)
    out = io.StringIO()
    with fake_world(world), contextlib.redirect_stdout(out):
        try:
            exec(compile(src, str(FW / sc["script"]), "exec"), {"__name__": "__main__"})
        except (SimStop, KeyboardInterrupt):
            pass
    return world, out.getvalue()


# ---- frames -> pictures ----------------------------------------------------
def to_image(buf, scale=3):
    panel = Image.frombytes("1", (128, 296), buf)                # rows of 16 bytes, 1 = white
    land = panel.rotate(270, expand=True).convert("L")           # inverse of img2badge's rotate(90)
    im = ImageOps.colorize(land, black=INK, white=PAPER)
    return im.resize((W * scale, H * scale), Image.NEAREST)


def kept_frames(frames):
    out, last = [], None
    for t, buf in frames:
        if buf == last or all(b == 0xFF for b in buf):           # repeats and blank seeds
            continue
        out.append((t, buf))
        last = buf
    return out or frames[-1:]


def sample_badge(path=None):
    if path:
        im = Image.open(path)
    else:
        im = Image.new("L", (W, H), 255)
        d = ImageDraw.Draw(im)
        d.rectangle([0, 0, W - 1, H - 1], outline=0, width=3)
        d.text((W // 2, 52), "MY BADGE", font=ImageFont.load_default(size=34), fill=0, anchor="mm")
        d.text((10, 8), "TOP-LEFT", font=ImageFont.load_default(size=14), fill=0)
        d.text((W - 10, H - 10), "BOTTOM-RIGHT", font=ImageFont.load_default(size=14), fill=0, anchor="rd")
        d.polygon([(10, 40), (40, 40), (25, 22)], fill=0)
    bw = _ib.to_1bit(im.resize((W, H)) if im.size != (W, H) else im, 128, False, False)
    return bw.rotate(90, expand=True).tobytes()


def scenarios(image):
    url = _st.uri_record("https://github.com/acornitum/outpost-badge")
    text = _st.text_record("Hello from the simulator!")
    nfc = bytes((0xE1, 0x40, 0x40, 0x01)) + bytes((0x03, len(url))) + url + b"\xfe"
    tap = lambda t, n: (t, t + 0.15, n)                                       # noqa: E731
    return [
        # taps start after the 2.4 s start-up refresh, during which the real menu is not polling
        dict(name="menu", script="menu/menu.py", limit=9, key=0.5,
             presses=[tap(3, "down"), tap(4, "down"), tap(5, "down"), tap(6, "down"), tap(7, "down")]),
        dict(name="snake", script="epaper/examples/snake.py", limit=9, key=0.7,
             presses=[tap(0.5, "right"), tap(1.4, "down"), tap(1.75, "left"), tap(2.9, "up"), tap(3.5, "right")]),
        dict(name="pong", script="epaper/examples/pong.py", limit=8, key=0.6,
             presses=[tap(0.3, "up"), (1.0, 1.8, "up"), (2.5, 3.5, "down"), (4.5, 5.2, "up")]),
        dict(name="flappy", script="epaper/examples/flappy.py", limit=8, key=0.6,
             patch=[("AUTO_PILOT = False", "AUTO_PILOT = True ")]),
        dict(name="ball", script="epaper/examples/ball.py", limit=4, key=0.5),
        dict(name="gradient", script="epaper/examples/gradient.py", limit=4, key=0.5),
        dict(name="nfc_text", script="nfc/examples/nfc_text.py", limit=14, key=0.5,
             phone=[(3.0, text), (8.0, url)]),
        dict(name="my_badge", script="my_badge/my_badge.py", limit=8, key=1.0,
             files={"/badge.bin": sample_badge(image), "/badge_nfc.bin": nfc}),
    ]


def save(sc, world, out):
    frames = kept_frames(world.frames)
    if not world.frames:
        print("  %-9s no frames captured" % sc["name"])
        return None
    pngs = [to_image(b, 3) for _, b in frames]
    key = frames[min(len(frames) - 1, int(round(sc.get("key", 0.5) * (len(frames) - 1))))]
    to_image(key[1], 3).save(out / (sc["name"] + ".png"))
    if len(frames) > 1:
        small = [to_image(b, 2) for _, b in frames][:60]
        times = [t for t, _ in frames][:60] + [frames[min(59, len(frames) - 1)][0] + 1.0]
        dur = [max(60, min(1800, int((times[i + 1] - times[i]) * 1000))) for i in range(len(small))]
        small[0].save(out / (sc["name"] + ".gif"), save_all=True, append_images=small[1:],
                      duration=dur, loop=0, optimize=False)
    print("  %-9s %3d frames captured, %2d distinct, %.1f s simulated"
          % (sc["name"], len(world.frames), len(frames), world.t))
    return to_image(key[1], 2)


def contact_sheet(items, out):
    font = ImageFont.load_default(size=15)
    cols, pad = 2, 18
    cw, ch = W * 2, H * 2 + 26
    rows = (len(items) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (cw + pad) + pad, rows * (ch + pad) + pad), (245, 245, 245))
    d = ImageDraw.Draw(sheet)
    for i, (name, im) in enumerate(items):
        x, y = pad + (i % cols) * (cw + pad), pad + (i // cols) * (ch + pad)
        sheet.paste(im, (x, y))
        d.text((x, y + H * 2 + 6), name, fill=(40, 40, 40), font=font)
    sheet.save(out / "contact_sheet.png")


def main():
    names = [n for n in ("menu", "snake", "pong", "flappy", "ball", "gradient", "nfc_text", "my_badge")]
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="preview", help="output folder (default ./preview)")
    ap.add_argument("--only", nargs="+", choices=names, help="run just these scenarios")
    ap.add_argument("--image", help="picture for the My Badge scenario (296x128 or any size)")
    ap.add_argument("--list", action="store_true", help="list scenarios and exit")
    a = ap.parse_args()
    if a.list:
        print("\n".join(names))
        return
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    items = []
    for sc in scenarios(a.image):
        if a.only and sc["name"] not in a.only:
            continue
        world, log = run(sc, sc.get("files"))
        im = save(sc, world, out)
        if im is not None:
            items.append((sc["name"], im))
        elif log:
            print(log[-600:])
    if items:
        contact_sheet(items, out)
        print("wrote %s (PNG + GIF per scenario, contact_sheet.png)" % out)


if __name__ == "__main__":
    main()
