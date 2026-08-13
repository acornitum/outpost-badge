# validate.py -- prove the corrected fast-partial path actually works.
#
# We know the timing is stable (300 frames, no timeouts) but NOT that it looks
# right or that it leaves the panel clean. Earlier, stripes were visible after a
# corrected run, but the panel already had damage from the broken runs, so the
# cause was ambiguous. The panel is reconditioned now, so this starts from a
# known-clean baseline and isolates one variable at a time.
#
# Each stage pauses so the panel can be inspected. Two passes: max speed
# (tpa=1) and stronger drive (tpa=3), to compare contrast and ghosting.

import time
import board
import busio
from ssd1680 import SSD1680, WIDTH, HEIGHT

STRIDE = WIDTH // 8
HOLD = 5.0            # seconds to hold each stage for inspection

spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=32_000_000)
epd.timeout = 3.0

print("\n=== validating fast partial refresh ===")
print("init ok:", epd.init())

white = bytearray(b"\xFF" * (STRIDE * HEIGHT))

# high-contrast checkerboard: big blocks, easy to judge crispness and ghosting
check = bytearray(STRIDE * HEIGHT)
for r in range(HEIGHT):
    off = r * STRIDE
    band = (r // 24) % 2
    row = bytearray(STRIDE)
    for bx in range(STRIDE):
        blk = (bx // 2) % 2
        row[bx] = 0x00 if (blk ^ band) else 0xFF
    check[off:off + STRIDE] = row


def bar_frame(pos, thick=32):
    """Solid bar at row `pos` on white -- a moving target for the motion test."""
    buf = bytearray(white)
    a = pos * STRIDE
    b = min((pos + thick) * STRIDE, len(buf))
    for i in range(a, b):
        buf[i] = 0x00
    return buf


def run_pass(tpa, label):
    print("\n########## PASS: %s (tpa=%d) ##########" % (label, tpa))

    print("[1/4] clean white baseline -- panel should be BLANK")
    epd.display_full(white)
    time.sleep(HOLD)

    epd.display_base(white)
    epd.arm_partial(tpa=tpa, frame_rate=0xFF, groups=1)

    print("[2/4] STATIC checkerboard via fast partial -- judge CONTRAST")
    epd.frame_nopower(check, mode=0x0C, sync_old=True)
    time.sleep(HOLD)

    print("[3/4] MOTION: 150 frames of a sweeping bar -- judge smoothness")
    pos = 0
    step = 8
    t0 = time.monotonic()
    fails = 0
    for i in range(150):
        _, _, ok = epd.frame_nopower(bar_frame(pos), mode=0x0C, sync_old=True)
        fails += (not ok)
        pos += step
        if pos + 32 >= HEIGHT or pos <= 0:
            step = -step
            pos += step
    dt = time.monotonic() - t0
    print("      %.1f fps, timeouts=%d" % (150 / dt, fails))
    epd.power_off()
    time.sleep(1.0)

    print("[4/4] single full refresh to WHITE -- ANY residual ghost?")
    epd.display_full(white)
    time.sleep(HOLD)


run_pass(1, "maximum speed")
run_pass(3, "stronger drive")

print("\n=== questions ===")
print("  * stage 2: was the checkerboard crisp, or faint/washed out?")
print("  * stage 3: did motion look clean?")
print("  * stage 4: after the white refresh, any ghost left behind?")
print("  * did tpa=3 look meaningfully better than tpa=1?")
