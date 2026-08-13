# flappy.py -- automated Flappy Bird on the e-paper panel.
#
# Display settings are the confirmed milestone (shape_probe.py): tpa=2, frame
# rate 0x44, 3 groups, sync_old=True, mode 0x04. Measured 9.4 fps, panel
# 99.8 ms, 40 ms of drive. The game runs at that tick and the physics constants
# suit it, not 60 fps.
#
# No buttons are wired, so an auto-pilot plays. It aims the bird at the middle
# of the next gap and flaps whenever the bird is below it.
#
# LOOKAHEAD is 0 on purpose. A 3-frame prediction was the first attempt and it
# died at the first pipe in every game: "by + vy*3 > target" fires a flap while
# the bird is still ~12 px ABOVE the target, and each flap lifts it 12 px more,
# so the oscillation centre climbs until the bird clips the top of the gap.
# With no prediction the bird oscillates around the target instead of above it.
#
# GRAVITY, FLAP and LOOKAHEAD come from a host-side parameter sweep, not from
# guesswork. At these values: 20 games x 20000 frames, zero deaths, and the
# bird stays inside y = 23..105 of a 0..127 panel.
#
# Drawing cost matters here, unlike the pre-rendered demos. Only ~7 ms of each
# frame is not panel time, so live drawing eats frame rate directly. The first
# version cost 21.4 ms of CPU and dropped the game to 7.8 fps. Two things fixed
# it, and both remove repeated work rather than making the work faster:
#   * the ground line is baked into a background buffer, not redrawn on 296
#     rows every frame;
#   * every row of a pipe is identical and a pipe's gap never changes while it
#     scrolls, so its 16-byte row is built once at spawn and then assigned.
#
# Orientation: landscape, FPC cable LEFT. The native row axis (296) runs
# horizontally on screen and the column axis (128) runs vertically. Screen
# coordinates below are (x across, y down). If the bird falls UPWARDS set
# FLIP_Y = True; if the pipes travel the wrong way set FLIP_X = True.

import random
import time
import board
import busio
import digitalio
from ssd1680 import SSD1680, WIDTH, HEIGHT

STRIDE = WIDTH // 8
NROWS = HEIGHT               # 296 -> screen width  (x, horizontal)
NCOLS = WIDTH                # 128 -> screen height (y, vertical)

FLIP_X = True
FLIP_Y = False

# ---- buttons ---------------------------------------------------------------
# Six switches on the badge. Any of them flaps.
#   left pad : up GP7, left GP8, right GP6, down GP9
#   right pad: A GP5, B GP4
# Wiring is switch-to-ground: internal pull-up, and a press reads low. The
# schematic could not settle this on its own -- it uses plain SW_Push symbols,
# and an open switch just follows whichever pull is enabled, so the resting
# level is the same either way. Confirmed on hardware by playing the game.
BUTTON_PINS = (board.GP7, board.GP8, board.GP6, board.GP9, board.GP5, board.GP4)
PULL_UP = True
PRESSED_LEVEL = False        # pin level while a button is held down
DEBOUNCE = 0.02              # seconds; ignore edges closer together than this
AUTO_PILOT = False           # True restores the computer player

BUTTONS = []
for _p in BUTTON_PINS:
    _io = digitalio.DigitalInOut(_p)
    _io.direction = digitalio.Direction.INPUT
    _io.pull = digitalio.Pull.UP if PULL_UP else digitalio.Pull.DOWN
    BUTTONS.append(_io)

_held = False                # debounced "any button is down"
_edge_t = 0.0                # time of the last accepted edge
_latch = False               # a press happened and the game has not used it
_presses = 0


def poll():
    """Sample the buttons and latch a press.

    This runs from inside the driver's BUSY wait, which is the ~100 ms of each
    frame when the CPU is idle waiting for the panel. Sampling there costs no
    frame rate, and it catches a tap shorter than one frame -- reading the
    buttons only between frames would lose those.

    Debounce is by time: after an accepted edge, further edges are ignored for
    DEBOUNCE seconds, so contact chatter cannot register as several presses.
    """
    global _held, _edge_t, _latch, _presses
    now = time.monotonic()
    if now - _edge_t < DEBOUNCE:
        return
    down = False
    for b in BUTTONS:
        if b.value == PRESSED_LEVEL:
            down = True
            break
    if down != _held:
        _held = down
        _edge_t = now
        if down:                 # act on the press, not the release
            _latch = True
            _presses += 1


def take_press():
    """Consume a latched press. One tap gives exactly one flap, and holding a
    button down does not repeat."""
    global _latch
    if _latch:
        _latch = False
        return True
    return False

# ---- gameplay, tuned for a ~9 fps tick ------------------------------------
BIRD_X = 72
BIRD_R = 7
GRAVITY = 2.0
FLAP = -8.0
VMAX = 12.0
PIPE_W = 26
GAP = 48
PIPE_SPEED = 10
SPACING = 148                # x distance between pipes
MARGIN = 10                  # smallest distance from a gap to an edge
LOOKAHEAD = 0                # see the note above -- prediction made it worse
GROUND_Y = NCOLS - 1

spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 3.0

print("\n=== flappy bird ===")
print("init ok:", epd.init())


# ---- drawing ---------------------------------------------------------------
def span(buf, off, y0, y1):
    """Set screen rows y0..y1 of the panel row at byte offset `off` to black.
    A vertical run on screen is a contiguous bit run inside one panel row, so
    this is the cheap primitive. Bit 1 = white, so black means clearing bits."""
    if FLIP_Y:
        y0, y1 = NCOLS - 1 - y1, NCOLS - 1 - y0
    if y0 < 0:
        y0 = 0
    if y1 > NCOLS - 1:
        y1 = NCOLS - 1
    if y1 < y0:
        return
    b0 = y0 >> 3
    b1 = y1 >> 3
    if b0 == b1:
        m = 0
        for c in range(y0, y1 + 1):
            m |= 0x80 >> (c & 7)
        buf[off + b0] &= 0xFF ^ m
        return
    m = 0
    for c in range(y0, (b0 << 3) + 8):
        m |= 0x80 >> (c & 7)
    buf[off + b0] &= 0xFF ^ m
    for b in range(b0 + 1, b1):
        buf[off + b] = 0x00
    m = 0
    for c in range(b1 << 3, y1 + 1):
        m |= 0x80 >> (c & 7)
    buf[off + b1] &= 0xFF ^ m


def rect(buf, x0, x1, y0, y1):
    """Filled rectangle in screen coordinates."""
    if FLIP_X:
        x0, x1 = NROWS - 1 - x1, NROWS - 1 - x0
    if x0 < 0:
        x0 = 0
    if x1 > NROWS - 1:
        x1 = NROWS - 1
    for x in range(x0, x1 + 1):
        span(buf, x * STRIDE, y0, y1)


def disc(buf, cx, cy, r):
    for dx in range(-r, r + 1):
        h = int((r * r - dx * dx) ** 0.5)
        rect(buf, cx + dx, cx + dx, cy - h, cy + h)


DIGITS = (
    ("111", "101", "101", "101", "111"),   # 0
    ("010", "110", "010", "010", "111"),   # 1
    ("111", "001", "111", "100", "111"),   # 2
    ("111", "001", "111", "001", "111"),   # 3
    ("101", "101", "111", "001", "001"),   # 4
    ("111", "100", "111", "001", "111"),   # 5
    ("111", "100", "111", "101", "111"),   # 6
    ("111", "001", "001", "001", "001"),   # 7
    ("111", "101", "111", "101", "111"),   # 8
    ("111", "101", "111", "001", "111"),   # 9
)


# NOTE on FLIP_X and text: no glyph mirroring is needed here, and adding it is
# a mistake. rect() maps screen x to panel row NROWS-1-x, and the viewer's
# left-to-right also runs backwards along the panel rows -- which is precisely
# why FLIP_X fixes the pipe direction. Those two inversions cancel for a static
# shape, so glyphs already land the right way round. An extra pre-mirror makes
# the digits read backwards.
def number(buf, value, x, y, scale=3):
    text = str(value)
    for k in range(len(text)):
        pat = DIGITS[ord(text[k]) - 48]
        ox = x + k * 4 * scale
        for ry in range(5):
            line = pat[ry]
            for cx in range(3):
                if line[cx] == "1":
                    rect(buf, ox + cx * scale, ox + cx * scale + scale - 1,
                         y + ry * scale, y + ry * scale + scale - 1)


# Background: white with the ground line already drawn. Built one time.
_bg = bytearray(b"\xFF" * (STRIDE * NROWS))
rect(_bg, 0, NROWS - 1, GROUND_Y - 1, GROUND_Y)
BG = bytes(_bg)
FRAME = bytearray(BG)


def pipe_row(top):
    """The 16 bytes shared by every row of a pipe with its gap at `top`.
    Built once when the pipe spawns; drawing the pipe is then 27 row copies."""
    row = bytearray(b"\xFF" * STRIDE)
    span(row, 0, 0, top)
    span(row, 0, top + GAP, GROUND_Y - 2)
    span(row, 0, GROUND_Y - 1, GROUND_Y)      # keep the ground line
    return bytes(row)


def new_pipe(x):
    top = random.randint(MARGIN, NCOLS - GAP - MARGIN)
    return [x, top, pipe_row(top)]


def draw(by, pipes, score):
    FRAME[:] = BG
    for p in pipes:
        pat = p[2]
        x0 = p[0]
        x1 = x0 + PIPE_W
        if FLIP_X:
            x0, x1 = NROWS - 1 - x1, NROWS - 1 - x0
        if x0 < 0:
            x0 = 0
        if x1 > NROWS - 1:
            x1 = NROWS - 1
        for x in range(x0, x1 + 1):
            off = x * STRIDE
            FRAME[off:off + STRIDE] = pat
    disc(FRAME, BIRD_X, int(by), BIRD_R)
    number(FRAME, score, 8, 8)


# ---- one round -------------------------------------------------------------
def play():
    by = NCOLS * 0.4
    vy = 0.0
    score = 0
    pipes = [new_pipe(NROWS + 40), new_pipe(NROWS + 40 + SPACING)]

    # Seed with the EMPTY background, not with the first game frame. This is a
    # 2.29 s factory full refresh, so whatever it shows is driven at full
    # strength and leaves the strongest ghost of anything on screen. Seeding
    # the first frame burned the pipes, bird and score in at full contrast and
    # held them there while the panel finished. Seeding BG keeps the panel
    # white (plus the static ground line) until the game is ready, and the
    # first partial frame then draws the scene normally.
    FRAME[:] = BG
    epd.display_base(BG)                    # seeds BOTH RAMs, panel = BG
    epd.arm_partial(tpa=2, frame_rate=0x44, groups=None)

    frames = 0
    cpu = 0.0
    # Classic start: hold the bird still until the first press, so the round
    # does not begin with the bird already falling.
    started = AUTO_PILOT
    take_press()
    while not started:
        poll()
        started = take_press()
    t_round = time.monotonic()
    while True:
        t_cpu = time.monotonic()

        if AUTO_PILOT:
            target = NCOLS * 0.4
            for p in pipes:
                if p[0] + PIPE_W >= BIRD_X - BIRD_R:
                    target = p[1] + GAP * 0.5
                    break
            if by + vy * LOOKAHEAD > target and by > BIRD_R + 4:
                vy = FLAP
        elif take_press():
            vy = FLAP

        vy += GRAVITY
        if vy > VMAX:
            vy = VMAX
        by += vy

        for p in pipes:
            p[0] -= PIPE_SPEED
        if pipes[0][0] + PIPE_W < 0:
            pipes.pop(0)
            pipes.append(new_pipe(pipes[-1][0] + SPACING))
            score += 1

        dead = by + BIRD_R >= GROUND_Y - 1 or by - BIRD_R <= 0
        for p in pipes:
            if p[0] - BIRD_R <= BIRD_X + BIRD_R and BIRD_X - BIRD_R <= p[0] + PIPE_W + BIRD_R:
                if by - BIRD_R < p[1] or by + BIRD_R > p[1] + GAP:
                    dead = True
        if dead:
            break

        draw(by, pipes, score)
        cpu += time.monotonic() - t_cpu
        epd.frame_nopower(FRAME, mode=0x04, sync_old=True, poll=poll)
        frames += 1
        if frames % 32 == 0:
            dt = time.monotonic() - t_round
            print("    score %3d | frame %5d | %.1f fps (%.1f ms/frame; cpu %.1f)"
                  " | presses %d"
                  % (score, frames, frames / dt, dt / frames * 1000,
                     cpu / frames * 1000, _presses))
    epd.power_off()
    return score, frames


try:
    best = 0
    game = 0
    while True:
        game += 1
        score, frames = play()
        if score > best:
            best = score
        print("game %d over: score %d in %d frames (best %d)"
              % (game, score, frames, best))
        FRAME[:] = BG
        number(FRAME, score, NROWS // 2 - 24, NCOLS // 2 - 20, 8)
        # init() restores the factory LUT from OTP. Without it a full refresh
        # runs the short custom waveform and is almost invisible.
        epd.init()
        epd.display_full(FRAME)
        take_press()
        t_wait = time.monotonic()
        while time.monotonic() - t_wait < 20.0:      # press to play again
            poll()
            if take_press():
                break
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
    epd.display_full(bytearray(BG))
    print("panel clean, analog off.")
