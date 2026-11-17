# menu.py -- launcher. Deployed as CIRCUITPY/code.py by `deploy.sh --menu`.
#
#   up / down   move the selection
#   A or right  start the highlighted app
#   (in an app) hold A + B for 1 s to come back here
#
# Apps live in CIRCUITPY/apps/. Starting one is supervisor.set_next_code_file()
# plus a reload: the app runs once as if it were code.py, and when it finishes
# (or A+B is held) it reloads, which runs this file again. See lib/badge.py.
#
# Display: the list is drawn with the fast partial path so moving the highlight
# is quick. A slightly stronger drive than the games (MENU_TPA) keeps text
# crisp. Ghosting builds up as the highlight moves, so every CLEAN_EVERY moves
# the screen gets a clean full refresh. The three rules in firmware/README.md
# apply: blank seed, sync_old=True, power_off() from a `finally`.

import os
import time
import board
import busio
from ssd1680 import SSD1680
import badge
import eink_text as et

MENU_TPA = 5              # drive length; games use 2. Higher = darker, slower
CLEAN_EVERY = 20          # full refresh after this many highlight moves
ROWS = 4                  # visible rows
ROW_H = 28
LIST_Y = 16
SCALE = 2

NAMES = {
    "flappy": "Flappy Bird",
    "snake": "Snake",
    "pong": "Pong",
    "ball": "Bouncing Ball",
    "gradient": "Gradient",
    "nfc_text": "NFC Text",
    "my_badge": "My Badge",
}


def find_apps():
    try:
        files = [f[:-3] for f in os.listdir("/apps") if f.endswith(".py") and not f.startswith("._")]
    except OSError:
        files = []
    order = list(NAMES)
    files.sort(key=lambda n: (order.index(n) if n in order else len(order), n))
    return files


def render(apps, sel, top):
    buf = et.blank()
    et.draw(buf, 2, 2, "OUTPOST BADGE", 1)
    hint = "up/down  A:start"
    et.draw(buf, 296 - len(hint) * 6 - 2, 2, hint, 1)
    et.fill_rect(buf, 0, LIST_Y - 2, 296, 1)
    for r in range(ROWS):
        i = top + r
        if i >= len(apps):
            break
        y = LIST_Y + r * ROW_H
        label = NAMES.get(apps[i], apps[i])
        if i == sel:
            et.fill_rect(buf, 0, y, 280, ROW_H - 2)
            et.draw(buf, 10, y + 1, label, SCALE, black=False)
        else:
            et.draw(buf, 10, y + 1, label, SCALE)
    if len(apps) > ROWS:                         # scroll bar
        track = ROWS * ROW_H - 2
        thumb = max(8, track * ROWS // len(apps))
        ty = LIST_Y + (track - thumb) * top // (len(apps) - ROWS)
        et.fill_rect(buf, 288, LIST_Y, 1, track)
        et.fill_rect(buf, 286, ty, 5, thumb)
    return buf


def no_apps():
    return et.render("No apps found.\nRun deploy.sh --menu")


spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 3.0
buttons = badge.Buttons()           # also waits for the launching press to end

apps = find_apps()
choice = None
print("\n=== menu === apps:", apps)

try:
    epd.init()
    if not apps:
        epd.display_full(no_apps())
        while True:
            time.sleep(1)

    sel, top, moves = 0, 0, 0
    buf = render(apps, sel, top)
    epd.display_base(et.blank())     # blank seed (README: never seed real content)
    epd.arm_partial(tpa=MENU_TPA, frame_rate=0x44, groups=None)
    epd.frame_nopower(buf, mode=0x04, sync_old=True)

    while True:
        try:
            buttons.poll()
        except KeyboardInterrupt:       # A+B held here does nothing useful
            buttons.exit.t = None
        new = sel
        if buttons.pressed("up"):
            new = (sel - 1) % len(apps)
        elif buttons.pressed("down"):
            new = (sel + 1) % len(apps)
        elif buttons.pressed("a") or buttons.pressed("right"):
            choice = apps[sel]
            break
        if new != sel:
            sel = new
            if sel < top:
                top = sel
            elif sel >= top + ROWS:
                top = sel - ROWS + 1
            buf = render(apps, sel, top)
            t0 = time.monotonic()
            epd.frame_nopower(buf, mode=0x04, sync_old=True, poll=buttons.poll)
            moves += 1
            print("move -> %s (%.2f s)" % (apps[sel], time.monotonic() - t0))
            if moves % CLEAN_EVERY == 0:
                epd.power_off()
                epd.init()
                epd.display_base(buf)     # full refresh that also re-seeds both RAMs
                epd.arm_partial(tpa=MENU_TPA, frame_rate=0x44, groups=None)
        time.sleep(0.01)
except KeyboardInterrupt:
    print("stopped.")
finally:
    epd.power_off()                 # rule 3

if choice:
    print("launch", choice)
    badge.launch("/apps/%s.py" % choice)
