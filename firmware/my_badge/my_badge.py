# my_badge.py -- show your own picture, and publish your NFC contact details.
#
# Data files on CIRCUITPY, made by img2badge.py on your computer:
#   /badge.bin       4736-byte 1-bit panel buffer  (required)
#   /badge_nfc.bin   CC + NDEF message for the tag (optional)
#
# On launch: publish the NFC data if there is any, then draw the image with one
# clean full refresh. E-paper holds the image with no power, so after that the
# app only waits for A+B (hold 1 s) to return to the menu.
#
# NFC is written the gentle way: only blocks that differ are rewritten, then the
# whole thing is read back and compared. A tag problem never stops the picture.
# RF writes are left enabled (as nfc_text needs); anyone's phone can overwrite
# the tag. Locking it is a deliberate non-goal for now.

import time
import board
import busio
from ssd1680 import SSD1680, WIDTH, HEIGHT
from st25dv import ST25DV
import eink_text
import badge

IMG = "/badge.bin"
NFC = "/badge_nfc.bin"
SIZE = (WIDTH // 8) * HEIGHT


def read_file(path):
    try:
        with open(path, "rb") as f:
            return f.read()
    except OSError:
        return None


def publish_nfc(data):
    i2c = busio.I2C(board.GP11, board.GP10, frequency=100_000)
    try:
        tag = ST25DV(i2c)
        n = tag.write_changed(0, data)
        ok = tag.read(0, len(data)) == bytes(data)
        print("NFC: %d bytes, %d blocks written, read-back %s"
              % (len(data), n, "verified" if ok else "FAILED"))
        return ok
    finally:
        i2c.deinit()


spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 5.0
buttons = badge.Buttons()

print("\n=== my badge ===")
img = read_file(IMG)
nfc = read_file(NFC)

if nfc:
    try:
        publish_nfc(nfc)
    except Exception as e:           # never let NFC problems affect the display
        print("NFC failed:", e)

print("init ok:", epd.init())
if img is None or len(img) != SIZE:
    print("no usable %s (%s)" % (IMG, "missing" if img is None else "%d bytes" % len(img)))
    epd.display_full(eink_text.render("No badge image yet\nRun img2badge.py\nthen reopen this"))
else:
    epd.display_full(bytearray(img))
print("done -- hold A+B to leave")

try:
    while True:
        buttons.poll()
        time.sleep(0.02)
except KeyboardInterrupt:
    print("stopped.")

badge.exit_to_menu()                 # no-op unless launched from the menu
