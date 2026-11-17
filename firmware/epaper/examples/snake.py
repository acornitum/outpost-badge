# snake.py -- Snake on the e-paper panel, at the fast-refresh tick (~9 fps).
#
# D-pad (up/left/right/down) steers. Any button starts a round. Hold A+B for
# 1 s to leave (back to the menu when launched from it).
#
# Field: 37 x 14 cells of 8 px under a 16 px score bar. Walls are deadly. One
# step per frame, so the snake moves ~9 cells/s; at most two turns are queued so
# a quick double tap (e.g. up then left) is not lost between frames.
#
# Display handling is the same as flappy.py and follows the three rules in
# firmware/README.md: seed with the blank background, arm_partial() with
# sync_old=True, and power_off() from a `finally`.

import random
import time
import board
import busio
from ssd1680 import SSD1680
import gfx
import badge
import eink_text

CELL = 8
GW = gfx.W // CELL                 # 37
BAR = 16                           # score bar height (px)
GH = (gfx.H - BAR) // CELL         # 14
UP, DOWN, LEFT, RIGHT = (0, -1), (0, 1), (-1, 0), (1, 0)

spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 3.0
buttons = badge.Buttons()

print("\n=== snake ===")
print("init ok:", epd.init())

# Static background: score-bar divider and a frame round the field.
_bg = gfx.blank()
gfx.rect(_bg, 0, gfx.W - 1, BAR - 2, BAR - 1)
gfx.rect(_bg, 0, gfx.W - 1, gfx.H - 1, gfx.H - 1)
gfx.rect(_bg, 0, 0, BAR, gfx.H - 1)
gfx.rect(_bg, gfx.W - 1, gfx.W - 1, BAR, gfx.H - 1)
BG = bytes(_bg)
FRAME = bytearray(BG)


def cell(gx, gy, size=6, inset=1):
    x = gx * CELL + inset
    y = BAR + gy * CELL + inset
    gfx.rect(FRAME, x, x + size - 1, y, y + size - 1)


def draw(snake, food, score):
    FRAME[:] = BG
    for gx, gy in snake:
        cell(gx, gy)
    # head is full-size so you can tell which end is which
    hx, hy = snake[0]
    cell(hx, hy, 8, 0)
    fx, fy = food
    x = fx * CELL + 1
    y = BAR + fy * CELL + 1
    gfx.rect(FRAME, x, x + 5, y, y)
    gfx.rect(FRAME, x, x + 5, y + 5, y + 5)
    gfx.rect(FRAME, x, x, y, y + 5)
    gfx.rect(FRAME, x + 5, x + 5, y, y + 5)
    gfx.rect(FRAME, x + 2, x + 3, y + 2, y + 3)
    gfx.number(FRAME, score, 6, 3, 2)


def spawn_food(snake):
    while True:
        f = (random.randint(0, GW - 1), random.randint(0, GH - 1))
        if f not in snake:
            return f


def next_dir(cur):
    """Newest queued turn that is not a reversal."""
    for name, d in (("up", UP), ("down", DOWN), ("left", LEFT), ("right", RIGHT)):
        if buttons.pressed(name) and (d[0] != -cur[0] or d[1] != -cur[1]) and d != cur:
            return d
    return cur


def play():
    snake = [(GW // 2, GH // 2), (GW // 2 - 1, GH // 2), (GW // 2 - 2, GH // 2)]
    d = RIGHT
    food = spawn_food(snake)
    score = 0

    epd.display_base(BG)                     # blank seed, see flappy.py
    epd.arm_partial(tpa=2, frame_rate=0x44, groups=None)
    draw(snake, food, score)
    epd.frame_nopower(FRAME, mode=0x04, sync_old=True, poll=buttons.poll)

    buttons.clear()
    while not buttons.any_pressed():         # wait for the first press
        buttons.poll()
        time.sleep(0.01)
    buttons.clear()

    frames = 0
    t0 = time.monotonic()
    while True:
        d = next_dir(d)
        head = (snake[0][0] + d[0], snake[0][1] + d[1])
        if not (0 <= head[0] < GW and 0 <= head[1] < GH) or head in snake[:-1]:
            break
        snake.insert(0, head)
        if head == food:
            score += 1
            food = spawn_food(snake)
        else:
            snake.pop()
        draw(snake, food, score)
        epd.frame_nopower(FRAME, mode=0x04, sync_old=True, poll=buttons.poll)
        frames += 1
        if frames % 32 == 0:
            print("    score %3d | frame %5d | %.1f fps"
                  % (score, frames, frames / (time.monotonic() - t0)))
    epd.power_off()
    return score


try:
    best = 0
    while True:
        score = play()
        best = max(best, score)
        print("game over: score %d (best %d)" % (score, best))
        epd.init()                            # factory LUT back before a full refresh
        epd.display_full(eink_text.render("Game over\nScore %d  Best %d\n\npress a button" % (score, best)))
        buttons.clear()
        while not buttons.any_pressed():
            buttons.poll()
            time.sleep(0.01)
except KeyboardInterrupt:
    print("\nstopped.")
finally:
    # rule 3: lower the supply from a `finally`, never only from an `except`
    epd.power_off()
    epd.init()
    epd.display_full(bytearray(BG))
    print("panel clean, analog off.")

badge.exit_to_menu()           # no-op unless launched from the menu
