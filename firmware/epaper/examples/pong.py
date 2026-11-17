# pong.py -- one-player Pong against the badge, at the fast-refresh tick.
#
# Up / down move your paddle (left side). First to 5 wins. Any button starts a
# round; hold A+B for 1 s to leave (back to the menu when launched from it).
#
# At ~9 fps the ball moves ~10 px per frame, which is more than a paddle is
# wide, so collisions are swept: we check whether the ball crossed the paddle's
# face between two frames instead of whether it overlaps one frame's position.
# The computer paddle is deliberately slower than the fastest shots so it can
# be beaten.
#
# Display handling follows the three rules in firmware/README.md.

import random
import time
import board
import busio
from ssd1680 import SSD1680
import gfx
import badge
import eink_text

W, H = gfx.W, gfx.H
PAD_W, PAD_H = 6, 28
PAD_SPEED = 10                       # player px per frame while held
CPU_SPEED = 6                        # computer px per frame
LX = 8                               # left paddle left edge
RX = W - 8 - PAD_W                   # right paddle left edge
BALL = 6
VX0, VXMAX = 8.0, 14.0
WIN = 5

spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 3.0
buttons = badge.Buttons()

print("\n=== pong ===")
print("init ok:", epd.init())

_bg = gfx.blank()
gfx.rect(_bg, 0, W - 1, 0, 1)                       # top and bottom walls
gfx.rect(_bg, 0, W - 1, H - 2, H - 1)
for y in range(4, H - 4, 12):                       # dashed centre line
    gfx.rect(_bg, W // 2 - 1, W // 2, y, y + 5)
BG = bytes(_bg)
FRAME = bytearray(BG)


def draw(ly, ry, bx, by, sl, sr):
    FRAME[:] = BG
    gfx.rect(FRAME, LX, LX + PAD_W - 1, int(ly), int(ly) + PAD_H - 1)
    gfx.rect(FRAME, RX, RX + PAD_W - 1, int(ry), int(ry) + PAD_H - 1)
    gfx.rect(FRAME, int(bx), int(bx) + BALL - 1, int(by), int(by) + BALL - 1)
    gfx.number(FRAME, sl, W // 2 - 40, 8, 4)
    gfx.number(FRAME, sr, W // 2 + 24, 8, 4)


def serve(direction):
    vy = random.choice((-1, 1)) * random.uniform(2.0, 4.0)
    return W / 2 - BALL / 2, H / 2 - BALL / 2, direction * VX0, vy


def clamp_pad(y):
    return max(2, min(H - 2 - PAD_H, y))


def play():
    sl = sr = 0
    ly = ry = (H - PAD_H) / 2
    bx, by, vx, vy = serve(random.choice((-1, 1)))

    epd.display_base(BG)
    epd.arm_partial(tpa=2, frame_rate=0x44, groups=None)
    draw(ly, ry, bx, by, sl, sr)
    epd.frame_nopower(FRAME, mode=0x04, sync_old=True, poll=buttons.poll)
    buttons.clear()
    while not buttons.any_pressed():
        buttons.poll()
        time.sleep(0.01)
    buttons.clear()

    frames = 0
    t0 = time.monotonic()
    while sl < WIN and sr < WIN:
        # player
        if buttons.held("up"):
            ly -= PAD_SPEED
        if buttons.held("down"):
            ly += PAD_SPEED
        ly = clamp_pad(ly)

        # computer: only chases when the ball is coming its way, else centres
        target = by + BALL / 2 - PAD_H / 2 if vx > 0 else (H - PAD_H) / 2
        step = max(-CPU_SPEED, min(CPU_SPEED, target - ry))
        ry = clamp_pad(ry + step)

        # ball
        px = bx
        bx += vx
        by += vy
        if by <= 2:
            by, vy = 2, -vy
        elif by >= H - 2 - BALL:
            by, vy = H - 2 - BALL, -vy

        if vx < 0 and px >= LX + PAD_W > bx:             # crossed the left face
            hit_y = by - vy * ((LX + PAD_W - bx) / -vx) if vx else by
            if ly - BALL <= hit_y <= ly + PAD_H:
                bx = LX + PAD_W
                vx = min(-vx * 1.06, VXMAX)
                vy += ((hit_y + BALL / 2) - (ly + PAD_H / 2)) / (PAD_H / 2) * 3.0
        elif vx > 0 and px + BALL <= RX < bx + BALL:     # crossed the right face
            hit_y = by
            if ry - BALL <= hit_y <= ry + PAD_H:
                bx = RX - BALL
                vx = -min(vx * 1.06, VXMAX)
                vy += ((hit_y + BALL / 2) - (ry + PAD_H / 2)) / (PAD_H / 2) * 3.0
        vy = max(-9.0, min(9.0, vy))

        if bx + BALL < 0:
            sr += 1
            bx, by, vx, vy = serve(-1)
        elif bx > W:
            sl += 1
            bx, by, vx, vy = serve(1)

        draw(ly, ry, bx, by, sl, sr)
        epd.frame_nopower(FRAME, mode=0x04, sync_old=True, poll=buttons.poll)
        frames += 1
        if frames % 32 == 0:
            print("    %d-%d | frame %5d | %.1f fps"
                  % (sl, sr, frames, frames / (time.monotonic() - t0)))
    epd.power_off()
    return sl, sr


try:
    while True:
        sl, sr = play()
        msg = "You win!" if sl > sr else "Computer wins"
        print("match over: %d-%d" % (sl, sr))
        epd.init()
        epd.display_full(eink_text.render("%s\n%d - %d\n\npress a button" % (msg, sl, sr)))
        buttons.clear()
        while not buttons.any_pressed():
            buttons.poll()
            time.sleep(0.01)
except KeyboardInterrupt:
    print("\nstopped.")
finally:
    epd.power_off()
    epd.init()
    epd.display_full(bytearray(BG))
    print("panel clean, analog off.")

badge.exit_to_menu()           # no-op unless launched from the menu
