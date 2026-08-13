# code.py -- windowing, done right: set 0x01 (gate-line count) BEFORE power-up.
#
# The earlier windowing test set the line count after power_on(), but 0x01 is
# latched when the analog powers up, so it never took effect and every window
# looked identical. arm_partial(lines=N) now sets it at the right moment.
#
# Safety: driving fewer lines changes no voltage and no waveform, and this runs
# the corrected path (DISPLAY Mode 2 + old-RAM sync), so static pixels stay at
# VSS. Short bursts, then a clean refresh.

import time
import board
import busio
from ssd1680 import SSD1680, WIDTH, HEIGHT

STRIDE = WIDTH // 8
spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=32_000_000)
epd.timeout = 3.0

print("\n=== windowing with 0x01 set before power-up ===")
print("init ok:", epd.init())

white = bytearray(b"\xFF" * (STRIDE * HEIGHT))
patt = bytearray(STRIDE * HEIGHT)
for r in range(HEIGHT):
    off = r * STRIDE
    patt[off:off + STRIDE] = (b"\x0F" * STRIDE) if (r // 8) % 2 else (b"\xF0" * STRIDE)

print("lines | write   | busy    |  fps   | ok")
for lines in (296, 200, 128, 96, 64, 32):
    epd.display_base(white)
    epd.arm_partial(tpa=1, frame_rate=0xFF, groups=1, lines=lines)
    w = b = 0.0
    oks = 0
    n = 8
    for i in range(n):
        src = patt if i % 2 else white
        tw, tb, ok = epd.frame_nopower(src, mode=0x0C, window=(0, lines - 1),
                                       sync_old=True)
        w += tw
        b += tb
        oks += ok
    epd.power_off()
    per = (w + b) / n
    print("%5d | %.5fs | %.5fs | %6.2f | %d/%d"
          % (lines, w / n, b / n, 1 / per, oks, n))

print("\ncleaning up.")
epd.display_full(white)
print("done.")
