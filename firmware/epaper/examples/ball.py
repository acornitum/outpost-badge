# ball.py -- bouncing ball, same display settings as the good gradient.
#
# Display configuration is identical to shape_probe.py, which is the confirmed
# milestone: tpa=2, frame rate 0x44, 3 groups, sync_old=True, mode 0x04.
# Measured there: 9.4 fps, panel 99.8 ms, 40 ms of drive. Only the picture is
# new, so any difference you see comes from the image, not from the settings.
#
# Orientation: landscape, FPC cable LEFT. The native row axis (NROWS=296) runs
# horizontally and the column axis (WIDTH=128) runs vertically. The ball moves
# vertically, so it moves along the COLUMN axis. A vertical span inside one row
# is a contiguous run of bits, so each row of the ball costs a few byte writes.
#
# Motion: free fall, not a sine. A sine spends equal time at the top and the
# bottom; a real ball hangs at the apex and reverses hard at the ground. Between
# impacts the ball is in free fall, so its height is a parabola:
#     height(t) = 4 * H * t * (1 - t),  t = 0 at the ground, t = 0.5 at the apex
# The ball touches the bottom, and at the apex its upper edge reaches 75 percent
# of the panel height. Frames are evenly spaced in t, so the steps are small at
# the top and large near the ground -- which is what free fall looks like.
#
# SQUASH deforms the ball on the contact frame: wider across, shorter up and
# down, base still on the ground. Set it to False for a rigid ball.
#
# One prediction to check: the gradient is made of dithered greys, and grey
# needs less drive than solid black. This ball is solid black, which is the
# harder case for a short waveform. It may look weaker than the gradient at the
# same setting. If it does, the fix is more drive time, not a different bug.

import math
import time
import board
import busio
from ssd1680 import SSD1680, WIDTH, HEIGHT

STRIDE = WIDTH // 8          # 16 bytes/row
NROWS = HEIGHT               # 296
NFRAMES = 16                 # frames held in RAM (~74 KB), 1 bounce per cycle
R = 16                       # ball radius, pixels
PEAK = 0.75                  # top edge reaches this fraction of the height
GROUND = WIDTH - 1           # column of the ground line
SQUASH = True                # deform the ball on the contact frame
SQ_WIDE = 1.30               # horizontal stretch when it hits
SQ_FLAT = 0.72               # vertical squash when it hits

spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 3.0

print("\n=== bouncing ball ===")
print("init ok:", epd.init())


def span(buf, row, c0, c1):
    """Set columns c0..c1 of `row` to black. Bit 1 = white, so this clears."""
    if c0 < 0:
        c0 = 0
    if c1 > WIDTH - 1:
        c1 = WIDTH - 1
    if c1 < c0:
        return
    off = row * STRIDE
    b0 = c0 >> 3
    b1 = c1 >> 3
    if b0 == b1:
        m = 0
        for c in range(c0, c1 + 1):
            m |= 0x80 >> (c & 7)
        buf[off + b0] &= 0xFF ^ m
        return
    m = 0
    for c in range(c0, (b0 << 3) + 8):
        m |= 0x80 >> (c & 7)
    buf[off + b0] &= 0xFF ^ m
    for b in range(b0 + 1, b1):
        buf[off + b] = 0x00
    m = 0
    for c in range(b1 << 3, c1 + 1):
        m |= 0x80 >> (c & 7)
    buf[off + b1] &= 0xFF ^ m


CX = NROWS // 2                      # ball stays centred horizontally
CY_LOW = WIDTH - 1 - R               # centre when the ball touches the ground
CY_HIGH = int(WIDTH * (1.0 - PEAK)) + R   # centre at the top of the bounce
GMASK = 0xFF ^ (0x80 >> (GROUND & 7))
GBYTE = GROUND >> 3

print("building %d frames (%.1f KB)..." % (NFRAMES, NFRAMES * STRIDE * NROWS / 1024))
t0 = time.monotonic()
FRAMES = []
for f in range(NFRAMES):
    # free fall: parabolic height, t=0 at the ground, t=0.5 at the apex
    t = f / float(NFRAMES)
    height = 4.0 * t * (1.0 - t)                 # 0 at the ground, 1 at the apex
    a = R                                        # semi-axis across (rows)
    b = R                                        # semi-axis up/down (columns)
    if SQUASH and f == 0:                        # contact frame: squash it
        a = int(R * SQ_WIDE + 0.5)
        b = int(R * SQ_FLAT + 0.5)
    cy = int(GROUND - b - (CY_LOW - CY_HIGH) * height + 0.5)
    buf = bytearray(b"\xFF" * (STRIDE * NROWS))
    for row in range(NROWS):                     # ground line, full width
        buf[row * STRIDE + GBYTE] &= GMASK
    for dr in range(-a, a + 1):                  # filled circle / ellipse
        row = CX + dr
        if row < 0 or row >= NROWS:
            continue
        h = int(b * (1.0 - (dr * dr) / float(a * a)) ** 0.5 + 0.5)
        span(buf, row, cy - h, cy + h)
    FRAMES.append(buf)
print("built in %.1fs" % (time.monotonic() - t0))

epd.display_base(FRAMES[0])                      # seeds BOTH RAMs
epd.arm_partial(tpa=2, frame_rate=0x44, groups=None)
print("looping -- Ctrl-C to stop.")

i = 0
shown = 0
busy = 0.0
t0 = time.monotonic()
try:
    while True:
        _, tb, _ = epd.frame_nopower(FRAMES[i % NFRAMES], mode=0x04, sync_old=True)
        busy += tb
        i += 1
        shown += 1
        if shown == NFRAMES:
            dt = time.monotonic() - t0
            print("    bounce %3d | frame %5d | %.1f fps (%.1f ms/frame; panel %.1f)"
                  % (i // NFRAMES, i, shown / dt, dt / shown * 1000,
                     busy / shown * 1000))
            t0 = time.monotonic()
            shown = 0
            busy = 0.0
except KeyboardInterrupt:
    print("\nstopped.")
finally:
    # ALWAYS drop the analog supply here, and in a `finally` rather than an
    # `except`. arm_partial() deliberately leaves the charge pump up and only
    # power_off() lowers it -- a full refresh powers down on its own, the fast
    # path does not. CircuitPython's auto-reload (which fires whenever
    # deploy.sh copies a file) and any unexpected exception both skip an
    # except-only handler and would leave the panel energised.
    epd.power_off()
    # init() restores the factory LUT from OTP. Without it this full refresh
    # runs the short custom waveform and barely marks the panel.
    epd.init()
    epd.display_full(bytearray(b"\xFF" * (STRIDE * NROWS)))
    print("panel clean, analog off.")
