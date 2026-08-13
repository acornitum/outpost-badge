# wave.py -- looping dithered gradient wave, video-ready.
#
# Fixes vs. the first version:
#   * single sine period across the axis -> ONE crest, no double peaks. (The
#     old triangle ramp folded 0->16->0, so a peak and a trough were on screen
#     at once and a second peak slid in as it scrolled.)
#   * DISPLAY Mode 2 + old-RAM sync -> static pixels stay undriven, so the panel
#     no longer walks toward black and freeze.
#   * zero allocation in the loop -> no GC stalls. (The earlier sweeping bar
#     allocated a 4736-byte frame per iteration, which is what made it pause at
#     random spots mid-screen.)
#
# Orientation: landscape, FPC cable LEFT -> the native row axis (296) runs
# horizontally, so the wave travels left->right.
#
# TPA is the contrast/speed knob: each extra LUT frame is ~5 ms of pigment
# drive. Dithered grays need far less drive than solid black, so a low TPA that
# looks washed out on a checkerboard can look right on a gradient.

import math
import time
import board
import busio
from ssd1680 import SSD1680, WIDTH, HEIGHT

TPA = 1                  # 1 = fastest/softest, higher = more contrast, slower
DURATION = 0             # seconds to animate; 0 = loop forever (no end-freeze)
WHOLE_CYCLES = True      # stop on a cycle boundary so the video loops seamlessly
CLEAN_AFTER = False      # True = full refresh at the end (flashes; clears ghosts)
PRECLEAN = 1             # black/white inversions before starting (0 to skip)
STRIDE = WIDTH // 8
NROWS = HEIGHT           # 296 = one full wave period
STEP = 4                 # rows/frame; multiple of 4 keeps the dither aligned
NFRAMES = NROWS // STEP  # 74 -> perfectly seamless loop
FULL_REFRESH_EVERY = 0   # frames; 0 disables (set e.g. 600 if ghosting builds)

spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=32_000_000)
epd.timeout = 3.0

print("\n=== gradient wave (tpa=%d) ===" % TPA)
print("init ok:", epd.init())

BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))

# 17 gray levels x 4 dither phases -> 16-byte row patterns
ROWPAT = []
for level in range(17):
    phases = []
    for r4 in range(4):
        row = bytearray(STRIDE)
        for bx in range(STRIDE):
            byte = 0
            for bit in range(8):
                if BAYER[r4][(bx * 8 + bit) & 3] >= level:
                    byte |= 0x80 >> bit          # 1 = white
            row[bx] = byte
        phases.append(bytes(row))
    ROWPAT.append(phases)

# Two periods of the wave, so any NROWS-long window is a valid frame. Stepping
# the window by a multiple of 4 preserves dither phase, so each frame is just a
# slice -- no per-frame math, no copies.
print("building wave (%d frames, %.1f KB)..." % (NFRAMES, 2 * NROWS * STRIDE / 1024))
BIG = bytearray(2 * NROWS * STRIDE)
for r in range(2 * NROWS):
    lvl = int((math.sin(2 * math.pi * (r % NROWS) / NROWS) + 1.0) * 8.0 + 0.5)
    if lvl > 16:
        lvl = 16
    off = r * STRIDE
    BIG[off:off + STRIDE] = ROWPAT[lvl][r & 3]

FRAME_BYTES = NROWS * STRIDE
VIEW = memoryview(BIG)

# ---- clean baseline so every run starts identically ----------------------
if PRECLEAN:
    white = bytearray(b"\xFF" * FRAME_BYTES)
    black = bytearray(FRAME_BYTES)
    print("pre-clean: %d black/white inversion(s)..." % PRECLEAN)
    for n in range(PRECLEAN):
        epd.display_full(black)
        if n < PRECLEAN - 1:
            epd.display_full(white)
    # Final white doubles as the RAM seed: display_base writes BOTH RAMs, so the
    # panel is white and the controller's reference agrees with it. The first
    # animated frame then transitions white -> frame 0 as a normal partial
    # update, with no extra full-refresh flash in between.
    epd.display_base(white)
else:
    epd.display_base(VIEW[0:FRAME_BYTES])    # seeds BOTH RAMs with frame 0
epd.arm_partial(tpa=TPA, frame_rate=0xFF, groups=1)

if DURATION:
    print("animating for %.0fs%s..."
          % (DURATION, " (rounded to whole cycles)" if WHOLE_CYCLES else ""))
else:
    print("looping -- Ctrl-C to stop.")

i = 0
shown = 0
t_run = time.monotonic()
t0 = t_run
try:
    while True:
        off = (i % NFRAMES) * STEP * STRIDE
        epd.frame_nopower(VIEW[off:off + FRAME_BYTES], mode=0x0C, sync_old=True)
        i += 1
        shown += 1
        if shown == NFRAMES:                 # report once per full loop
            dt = time.monotonic() - t0
            print("  cycle %d: %.1f fps (%.1f ms/frame)"
                  % (i // NFRAMES, shown / dt, dt / shown * 1000))
            t0 = time.monotonic()
            shown = 0
        # stop on time; if WHOLE_CYCLES, only at a cycle boundary so the
        # animation ends exactly where it began and the video loops cleanly
        if DURATION and (time.monotonic() - t_run) >= DURATION:
            if not WHOLE_CYCLES or i % NFRAMES == 0:
                break
        if FULL_REFRESH_EVERY and i % FULL_REFRESH_EVERY == 0:
            epd.power_off()
            epd.display_base(VIEW[off:off + FRAME_BYTES])
            epd.arm_partial(tpa=TPA, frame_rate=0xFF, groups=1)
            t0 = time.monotonic()
            shown = 0
    total = time.monotonic() - t_run
    epd.power_off()
    print("done: %d frames in %.1fs (%.1f fps, %.1f cycles)"
          % (i, total, i / total, i / NFRAMES))
    if CLEAN_AFTER:
        epd.display_full(bytearray(b"\xFF" * FRAME_BYTES))
        print("panel cleared.")
    else:
        print("last frame left on screen (run recondition.py if ghosting shows).")
except KeyboardInterrupt:
    epd.power_off()
    epd.display_full(VIEW[0:FRAME_BYTES])
    print("stopped; panel left clean.")
