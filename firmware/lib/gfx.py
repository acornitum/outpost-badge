"""Screen-coordinate drawing for the fast-refresh games (snake, pong, ...).

Landscape, FPC cable LEFT, same orientation as flappy.py (FLIP_X = True,
FLIP_Y = False): screen x runs 0..295 left to right and maps to panel row
295-x; screen y runs 0..127 top to bottom and is the column inside that row.
A vertical run on screen is a contiguous bit run inside one panel row, which
is the cheap primitive. Bit 1 = white, so drawing black clears bits.
"""
W = 296                      # screen width  (panel rows)
H = 128                      # screen height (panel columns)
STRIDE = H // 8              # 16 bytes per panel row


def blank():
    return bytearray(b"\xFF" * (STRIDE * W))


def span(buf, off, y0, y1):
    """Set screen rows y0..y1 of the panel row at byte offset `off` to black."""
    if y0 < 0:
        y0 = 0
    if y1 > H - 1:
        y1 = H - 1
    if y1 < y0:
        return
    b0, b1 = y0 >> 3, y1 >> 3
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
    """Filled black rectangle, inclusive screen coordinates."""
    x0, x1 = W - 1 - x1, W - 1 - x0
    if x0 < 0:
        x0 = 0
    if x1 > W - 1:
        x1 = W - 1
    for x in range(x0, x1 + 1):
        span(buf, x * STRIDE, y0, y1)


def disc(buf, cx, cy, r):
    for dx in range(-r, r + 1):
        h = int((r * r - dx * dx) ** 0.5)
        rect(buf, cx + dx, cx + dx, cy - h, cy + h)


DIGITS = (
    ("111", "101", "101", "101", "111"),
    ("010", "110", "010", "010", "111"),
    ("111", "001", "111", "100", "111"),
    ("111", "001", "111", "001", "111"),
    ("101", "101", "111", "001", "001"),
    ("111", "100", "111", "001", "111"),
    ("111", "100", "111", "101", "111"),
    ("111", "001", "001", "001", "001"),
    ("111", "101", "111", "101", "111"),
    ("111", "101", "111", "001", "111"),
)


def number(buf, value, x, y, scale=3):
    """3x5 digits scaled up; returns the drawn width."""
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
    return len(text) * 4 * scale - scale
