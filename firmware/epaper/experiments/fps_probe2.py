# fps_probe2.py -- fps_probe + exactly one change: groups 3 -> 1.
#
# Exactly ONE variable is changed from the run that was confirmed to look good:
# the frame count. Everything else is byte-for-byte the original -- same 16
# precomputed frames, same folded-triangle ramp along the ROW axis (so the
# double peaks are back; that is intentional, this is the known-good baseline),
# same 20 MHz SPI, same DISPLAY Mode 1 (0x04), same *absent* old-RAM sync.
#
# The original did not fail. It was a fixed 120-frame run that finished:
#     --> maximum (tpa=1 fr=0xFF groups=1)
#         18.4 fps end-to-end (54.3 ms/frame)
#     done -- clean full refresh.
#     Code done running.
# It "froze" because it ran out of frames and exited, not because of the panel.
#
# What we are testing here: does that same picture hold up past 120 frames, and
# if it degrades, how does it degrade and when? Do NOT add a fix to this file.
# If it walks toward black, that is the result -- record it, then change one
# variable in a separate file and compare against this.
#
# Ctrl-C is the stop. It powers down and leaves a clean full refresh.

import math
import time
import board
import busio
from ssd1680 import SSD1680, WIDTH, HEIGHT

STRIDE = WIDTH // 8          # 16 bytes/row
NROWS = HEIGHT               # 296
NFRAMES = 16                 # animation frames held in RAM (~74 KB)
REPORT_EVERY = NFRAMES       # report once per wave cycle, so a cycle number on
                             # the console can be matched to what the panel is
                             # doing. Logging only -- the display path is
                             # untouched.

# (label, tpa, frame_rate, groups, frames);  frames = 0 -> loop forever
SETTINGS = (
    ("groups=1", 1, 0x44, 1,    0),
)

spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 3.0

print("\n=== FULL-SCREEN gradient PoC (baseline, looping) ===")
print("init ok:", epd.init())

# ---- 4x4 Bayer dither -> per (level, row%4) 16-byte row patterns -----------
BAYER = ((0, 8, 2, 10), (12, 4, 14, 6), (3, 11, 1, 9), (15, 7, 13, 5))

print("building dither patterns...")
ROWPAT = []                                  # ROWPAT[level][row % 4] -> bytes
for level in range(17):
    per_phase = []
    for r4 in range(4):
        row = bytearray(STRIDE)
        for bx in range(STRIDE):
            byte = 0
            for bit in range(8):
                col = bx * 8 + bit
                # black where the dither threshold is under the level
                if BAYER[r4][col & 3] >= level:
                    byte |= 0x80 >> bit      # 1 = white
            row[bx] = byte
        per_phase.append(bytes(row))
    ROWPAT.append(per_phase)

# ---- precompute the animation frames --------------------------------------
print("precomputing %d frames..." % NFRAMES)
t0 = time.monotonic()
FRAMES = []
for f in range(NFRAMES):
    phase = f * (NROWS // NFRAMES)
    buf = bytearray(STRIDE * NROWS)
    for row in range(NROWS):
        t = (row + phase) % NROWS
        # ONE sine period across NROWS -> a single peak, no trough beside it
        lvl = int((math.sin(2 * math.pi * t / NROWS) + 1.0) * 8.0 + 0.5)
        if lvl > 16:
            lvl = 16
        off = row * STRIDE
        buf[off:off + STRIDE] = ROWPAT[lvl][row & 3]
    FRAMES.append(buf)
print("built in %.1fs (%d KB)" % (time.monotonic() - t0,
                                  NFRAMES * STRIDE * NROWS // 1024))

# ---- play it back ---------------------------------------------------------
try:
    for label, tpa, fr, groups, frames in SETTINGS:
        epd.display_base(FRAMES[0])
        epd.arm_partial(tpa=tpa, frame_rate=fr, groups=groups)
        print("\n--> %s (tpa=%d fr=0x%02X groups=%s)%s"
              % (label, tpa, fr, groups, "" if frames else "  [looping]"))
        i = 0
        shown = 0
        busy = 0.0
        t0 = time.monotonic()
        while frames == 0 or i < frames:
            # sync_old=False restores the original behaviour: the driver gained
            # a sync_old parameter after this run, defaulting to True.
            _, tb, _ = epd.frame_nopower(FRAMES[i % NFRAMES], mode=0x04,
                                         sync_old=True)
            busy += tb
            i += 1
            shown += 1
            if shown == REPORT_EVERY:
                dt = time.monotonic() - t0
                print("    cycle %3d | frame %5d | %.1f fps (%.1f ms/frame; panel %.1f, rest %.1f)"
                      % (i // NFRAMES, i, shown / dt, dt / shown * 1000,
                         busy / shown * 1000, (dt - busy) / shown * 1000))
                t0 = time.monotonic()
                shown = 0
                busy = 0.0
        epd.power_off()
        time.sleep(1.0)
    print("\ndone -- clean full refresh.")
    epd.display_full(FRAMES[0])
except KeyboardInterrupt:
    epd.power_off()
    print("\nstopped -- clean full refresh.")
    epd.display_full(FRAMES[0])
