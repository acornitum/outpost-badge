"""Draw wrapped, centred text onto an SSD1680 panel buffer (landscape view).

The font is CircuitPython's built-in terminalio.FONT (6x12 cells, ASCII
32..126), scaled up by an integer factor, so no font file is needed.

Orientation matches epaper/examples/flappy.py with FLIP_X = True: landscape,
FPC cable to the left, screen x -> panel row (NROWS-1-x), screen y -> column.
Glyphs land the right way round with no extra mirroring (see the note in
flappy.py about FLIP_X and text).
"""
import terminalio

NROWS = 296                 # screen width  (x)
NCOLS = 128                 # screen height (y)
STRIDE = NCOLS // 8
GW, GH = 6, 12              # glyph cell of terminalio.FONT
SCALES = (4, 3, 2, 1)

_cache = {}


def _glyph(ch):
    pts = _cache.get(ch)
    if pts is None:
        g = terminalio.FONT.get_glyph(ord(ch))
        pts = []
        if g is not None:
            bm, base = g.bitmap, g.tile_index * GW
            for y in range(GH):
                for x in range(GW):
                    if bm[base + x, y]:
                        pts.append((x, y))
        _cache[ch] = pts
    return pts


def clean(text):
    """ASCII only; anything else becomes '?'. Tabs and CR are dropped."""
    out = []
    for ch in text:
        if ch == "\n" or 32 <= ord(ch) < 127:
            out.append(ch)
        elif ch in "‘’":
            out.append("'")
        elif ch in "“”":
            out.append('"')
        elif ch not in "\r\t":
            out.append("?")
    return "".join(out)


def wrap(text, cols):
    lines = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            while len(word) > cols:                 # hard-break long words/URLs
                if cur:
                    lines.append(cur)
                    cur = ""
                lines.append(word[:cols])
                word = word[cols:]
            if not cur:
                cur = word
            elif len(cur) + 1 + len(word) <= cols:
                cur += " " + word
            else:
                lines.append(cur)
                cur = word
        lines.append(cur)
    return lines


def layout(text):
    """Largest scale at which the wrapped text fits. Returns (scale, lines);
    at scale 1 anything that still does not fit is cut with '...'."""
    text = clean(text)
    for sc in SCALES:
        cols, rows = NROWS // (GW * sc), NCOLS // (GH * sc)
        lines = wrap(text, cols)
        if len(lines) <= rows:
            return sc, lines
    lines = lines[:rows]
    lines[-1] = lines[-1][:max(cols - 3, 0)] + "..."
    return 1, lines


def blank():
    return bytearray(b"\xFF" * (STRIDE * NROWS))


def _block(buf, x, y, sc, black=True):
    for sx in range(sc):
        X = x + sx
        if X < 0 or X >= NROWS:
            continue
        base = (NROWS - 1 - X) * STRIDE
        for sy in range(sc):
            Y = y + sy
            if 0 <= Y < NCOLS:
                if black:
                    buf[base + (Y >> 3)] &= ~(0x80 >> (Y & 7)) & 0xFF
                else:
                    buf[base + (Y >> 3)] |= 0x80 >> (Y & 7)


def fill_rect(buf, x, y, w, h, black=True):
    for X in range(max(x, 0), min(x + w, NROWS)):
        base = (NROWS - 1 - X) * STRIDE
        for Y in range(max(y, 0), min(y + h, NCOLS)):
            if black:
                buf[base + (Y >> 3)] &= ~(0x80 >> (Y & 7)) & 0xFF
            else:
                buf[base + (Y >> 3)] |= 0x80 >> (Y & 7)


def draw(buf, x, y, text, scale=1, black=True):
    """Draw one line of text with its top-left at screen (x, y). Returns the
    width in pixels. black=False draws white ink, for text on a black bar."""
    text = clean(text)
    cw = GW * scale
    for k, ch in enumerate(text):
        for gx, gy in _glyph(ch):
            _block(buf, x + k * cw + gx * scale, y + gy * scale, scale, black)
    return len(text) * cw


def render(text):
    """Return a panel buffer with `text` wrapped and centred."""
    sc, lines = layout(text)
    buf = blank()
    cw, lh = GW * sc, GH * sc
    y0 = (NCOLS - len(lines) * lh) // 2
    for n, line in enumerate(lines):
        x0 = (NROWS - len(line) * cw) // 2
        for k, ch in enumerate(line):
            for gx, gy in _glyph(ch):
                _block(buf, x0 + k * cw + gx * sc, y0 + n * lh + gy * sc, sc)
    return buf
