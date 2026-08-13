# power_probe.py -- BASELINE + exactly one change: re-assert analog power.
#
# Baseline (replay_accum.py) result: the wave animates for ~4-5 s (~75-90
# frames) at 18.4 fps and then motion stops dead, while the firmware keeps
# issuing frames indefinitely (verified past frame 1860, still 18.4 fps, panel
# busy still ~50 ms, no timeouts). Reported look of the failure: "as if someone
# just unplugged power" -- abrupt, not a fade.
#
# That is the one thing this file tests. The ONLY difference from the baseline
# is that epd.power_on() is re-issued every REPOWER_EVERY frames.
#
# power_on() is the minimal isolated change available: it is just
#     0x22 <- 0xC0   (enable clock + analog)
#     0x20           (master activation)
# No hardware reset, no RAM write, no LUT reload, no waveform change. So a
# difference in behaviour can only be attributed to the analog/charge-pump
# state, and to nothing else.
#
# Read the result off the panel, no console needed:
#   * motion continues past ~5 s indefinitely -> analog power was the cause.
#   * motion still dies at ~4-5 s             -> it is NOT the charge pump;
#                                                the next variable is elsewhere.
#
# Everything else is byte-for-byte the baseline: same 16 precomputed frames,
# same folded-triangle ramp on the ROW axis (double peaks intentional), same
# 20 MHz SPI, same DISPLAY Mode 1 (0x04), same absent old-RAM sync.

import time
import board
import busio
from ssd1680 import SSD1680, WIDTH, HEIGHT

STRIDE = WIDTH // 8          # 16 bytes/row
NROWS = HEIGHT               # 296
NFRAMES = 16                 # animation frames held in RAM (~74 KB)
REPORT_EVERY = NFRAMES       # one report per wave cycle (logging only)
REPOWER_EVERY = 60           # <-- THE ONLY CHANGE. 0 disables = plain baseline.

# (label, tpa, frame_rate, groups, frames);  frames = 0 -> loop forever
SETTINGS = (
    ("balanced", 2, 0x44, None, 60),
    ("maximum ", 1, 0xFF, 1,     0),
)

spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 3.0

print("\n=== gradient PoC: re-assert analog power every %d frames ===" % REPOWER_EVERY)
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
        lvl = t * 32 // NROWS                # triangle wave 0..16..0
        if lvl > 16:
            lvl = 32 - lvl
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
                                         sync_old=False)
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
            if REPOWER_EVERY and i % REPOWER_EVERY == 0:
                # THE ONLY CHANGE vs. baseline: clock + analog re-asserted.
                # Nothing is reset, rewritten, or reloaded.
                epd.power_on()
                t0 = time.monotonic()        # keep the fps figure honest
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
