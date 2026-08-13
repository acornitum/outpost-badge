# ladder.py -- find the fastest setting that still produces a VISIBLE image.
#
# Total pigment drive per update = TPA frames x frame period(frame_rate).
# TPA and the frame-rate bytes are therefore the SAME knob: total drive time.
# The factory partial waveform uses ~400 ms; tpa=1 @ 0xFF is ~5 ms, which is why
# it produced only a faint gray haze with no image.
#
# This runs the same wave at decreasing drive times so the trade can be judged
# directly. Each step announces itself and animates for a few seconds.

import math
import time
import board
import busio
from ssd1680 import SSD1680, WIDTH, HEIGHT

STRIDE = WIDTH // 8
NROWS = HEIGHT
STEP = 4
NFRAMES = NROWS // STEP
SECONDS_EACH = 5.0

spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=32_000_000)
epd.timeout = 6.0
print("\n=== drive-time ladder ===")
print("init ok:", epd.init())

BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))
ROWPAT = []
for level in range(17):
    phases = []
    for r4 in range(4):
        row = bytearray(STRIDE)
        for bx in range(STRIDE):
            byte = 0
            for bit in range(8):
                if BAYER[r4][(bx * 8 + bit) & 3] >= level:
                    byte |= 0x80 >> bit
            row[bx] = byte
        phases.append(bytes(row))
    ROWPAT.append(phases)

BIG = bytearray(2 * NROWS * STRIDE)
for r in range(2 * NROWS):
    lvl = int((math.sin(2 * math.pi * (r % NROWS) / NROWS) + 1.0) * 8.0 + 0.5)
    if lvl > 16:
        lvl = 16
    off = r * STRIDE
    BIG[off:off + STRIDE] = ROWPAT[lvl][r & 3]

FRAME_BYTES = NROWS * STRIDE
VIEW = memoryview(BIG)

# (tpa, frame_rate, approx drive ms) -- most drive (best contrast) first
LADDER = (
    (10, 0x22, 400),
    (5,  0x22, 200),
    (4,  0x44, 92),
    (10, 0xFF, 50),
    (5,  0xFF, 25),
    (2,  0xFF, 10),
)

for tpa, fr, drive in LADDER:
    epd.display_base(VIEW[0:FRAME_BYTES])       # flash marks a new setting
    epd.arm_partial(tpa=tpa, frame_rate=fr, groups=1)
    print("\n--> drive ~%3d ms  (tpa=%d fr=0x%02X)" % (drive, tpa, fr))
    i = 0
    t0 = time.monotonic()
    while time.monotonic() - t0 < SECONDS_EACH:
        off = (i % NFRAMES) * STEP * STRIDE
        epd.frame_nopower(VIEW[off:off + FRAME_BYTES], mode=0x0C, sync_old=True)
        i += 1
    dt = time.monotonic() - t0
    epd.power_off()
    print("    %.1f fps  (%.0f ms/frame)" % (i / dt, dt / i * 1000))

print("\ncleaning up.")
epd.display_full(bytearray(b"\xFF" * FRAME_BYTES))
print("Which was the FASTEST step where the wave was still clearly visible?")
