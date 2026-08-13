# compare.py -- A/B contrast comparison at equal speed, with letter cards.
#
# Both settings run at 9.4 fps. Only the use of the time differs, so what you
# see is contrast and nothing else:
#   A: tpa=2, 3 groups -> 40 ms drive + the two hold phases
#   B: tpa=3, 1 group  -> 60 ms drive, no hold phases
#
# Sequence for each setting: white flash, the letter, white flash, 10 s run.
# The pair repeats until you stop it.
#
# IMPORTANT (a real driver defect this test exposed): arm_partial() writes the
# custom LUT into 0x32 and the voltage registers, and nothing puts the factory
# waveform back. A plain display_full() after it therefore runs the SHORT
# partial waveform: measured 0.613 s instead of ~2.3 s, which is too weak to
# see. epd.init() does a software reset and restores the factory LUT from OTP,
# so every full refresh here is preceded by init().
#
# The per-frame display path is identical to shape_probe.py.
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

HOLD = 1.5               # seconds the letter card stays on screen
SCALE = 14               # letter pixel size

# (letter, tpa, frame_rate, groups, seconds)
SETTINGS = (
    ("A", 2, 0x44, None, 10.0),
    ("B", 3, 0x44, 1,    10.0),
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

# ---- letter cards ---------------------------------------------------------
FONT = {
    "A": ("01110", "10001", "10001", "11111", "10001", "10001", "10001"),
    "B": ("11110", "10001", "10001", "11110", "10001", "10001", "11110"),
}


def letter_frame(ch, scale=SCALE):
    """Big centred letter, black on white.

    Screen orientation: landscape with the cable LEFT, so the native row axis
    (NROWS=296) runs horizontally and the column axis (WIDTH=128) runs
    vertically. Letter column -> panel row, letter row -> panel column.
    The letter may come out rotated or mirrored; A and B stay distinct anyway.
    """
    buf = bytearray(b"\xFF" * (STRIDE * NROWS))
    rows = FONT[ch]
    x0 = (NROWS - len(rows[0]) * scale) // 2
    y0 = (WIDTH - len(rows) * scale) // 2
    for ry in range(len(rows)):
        line = rows[ry]
        for cx in range(len(line)):
            if line[cx] != "1":
                continue
            for j in range(scale):
                sy = y0 + ry * scale + j
                mask = 0xFF ^ (0x80 >> (sy & 7))
                base = (sy >> 3)
                for i in range(scale):
                    sx = x0 + cx * scale + i
                    buf[sx * STRIDE + base] &= mask
    return buf


WHITE = bytearray(b"\xFF" * (STRIDE * NROWS))
CARDS = {}
for _ch in FONT:
    CARDS[_ch] = letter_frame(_ch)


def full(buf):
    """Factory-waveform full refresh. init() restores the OTP LUT first."""
    epd.init()
    return epd.display_full(buf)


# ---- A / B / A / B ... ----------------------------------------------------
try:
    while True:
        for letter, tpa, fr, groups, secs in SETTINGS:
            full(WHITE)
            full(CARDS[letter])
            time.sleep(HOLD)
            full(WHITE)
            epd.init()
            epd.display_base(FRAMES[0])       # seeds BOTH RAMs, shows frame 0
            epd.arm_partial(tpa=tpa, frame_rate=fr, groups=groups)
            print("\n--> %s (tpa=%d fr=0x%02X groups=%s)"
                  % (letter, tpa, fr, groups))
            i = 0
            busy = 0.0
            t0 = time.monotonic()
            t_end = t0 + secs
            while time.monotonic() < t_end:
                _, tb, _ = epd.frame_nopower(FRAMES[i % NFRAMES], mode=0x04,
                                             sync_old=True)
                busy += tb
                i += 1
            dt = time.monotonic() - t0
            epd.power_off()
            print("    %d frames | %.1f fps (%.1f ms/frame; panel %.1f)"
                  % (i, i / dt, dt / i * 1000, busy / i * 1000))
except KeyboardInterrupt:
    print("\nstopped.")
finally:
    # See the note in epaper/examples/ball.py: this must be `finally`, not `except`.
    # arm_partial() leaves the charge pump up and only power_off() lowers it,
    # and auto-reload skips an except-only handler. full() does the init() that
    # restores the factory waveform.
    epd.power_off()
    full(WHITE)
    print("panel clean, analog off.")
