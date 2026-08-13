# recondition.py -- clear ghosting / accumulated bias from the panel.
#
# Run this whenever the panel shows residual ghost images. It resets the
# controller (SW reset restores the factory OTP waveform, so the clearing is
# done with the full-strength LUT, never a shortened one) and then drives the
# panel fully to black and fully to white several times. Each inversion pulls
# pigment hard to both rails, which is what actually clears a ghost -- a single
# white refresh does not.
#
# Usage: copy to CIRCUITPY as code.py, or  `import recondition`  from the REPL.

import board
import busio
from ssd1680 import SSD1680, WIDTH, HEIGHT

CYCLES = 8

STRIDE = WIDTH // 8
spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 8.0

print("\n=== reconditioning panel ===")
print("init (SW reset -> factory OTP waveform):", epd.init())

white = bytearray(b"\xFF" * (STRIDE * HEIGHT))
black = bytearray(STRIDE * HEIGHT)

for i in range(CYCLES):
    dt_b, _ = epd.display_base(black)     # display_base seeds BOTH RAMs, so the
    dt_w, _ = epd.display_base(white)     # old/new reference can't stay stale
    print("cycle %d/%d  (black %.2fs, white %.2fs)" % (i + 1, CYCLES, dt_b, dt_w))

epd.display_base(white)
print("done -- panel should be uniformly white, no stripes.")
