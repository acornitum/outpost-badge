# nfc_text.py -- the badge shows whatever a phone writes to its NFC tag.
#
# Write a Text record (or a URL, or a vCard) to the badge with an NFC app such
# as "NFC Tools" (Android and iOS: Write -> Add a record -> Text). A few
# seconds after the phone is removed, the text appears on the e-paper.
#
# How it works: the ST25DV tag memory is shared between the RF side (phone) and
# the I2C side (this MCU). Every POLL seconds we read the tag, pull out the
# NDEF message and compare it with what is on screen. A change must be seen on
# two polls in a row before we redraw, so a half-finished phone write does not
# cost a 2.3 s refresh. While the phone is mid-write the chip NACKs I2C; that is
# normal and is simply skipped.
#
# Display: one clean full refresh per change. init() runs first every time, so
# no custom waveform is loaded (rule 2 in firmware/README.md); a full refresh
# powers the panel down by itself, so the fast-path power_off() rule is not
# involved here.
#
# The tag must allow RF writes. It does by default; img2eink's --publish mode
# turns RF write protection on and has to be undone before this will work.

import time
import board
import busio
from ssd1680 import SSD1680
from st25dv import ST25DV, scan_ndef, describe
import eink_text
import badge

POLL = 0.4
PROMPT = "Write text to me\nwith your phone!"

i2c = busio.I2C(board.GP11, board.GP10, frequency=100_000)
nfc = ST25DV(i2c)
spi = busio.SPI(clock=board.GP14, MOSI=board.GP15)
epd = SSD1680(spi, board.GP13, board.GP17, board.GP18, board.GP16, baudrate=20_000_000)
epd.timeout = 5.0

print("\n=== nfc text display ===")
print("tag UID:", nfc.uid().hex(), "mem:", nfc.mem_bytes())


def show(msg):
    text = describe(msg) if msg else None
    if not text:
        text = PROMPT
    print("show:", repr(text))
    t0 = time.monotonic()
    buf = eink_text.render(text)
    t1 = time.monotonic()
    epd.init()
    epd.display_full(buf)
    print("  render %.2f s, refresh %.2f s" % (t1 - t0, time.monotonic() - t1))


def read_msg():
    try:
        return True, scan_ndef(nfc.read(0, 512))
    except OSError:                  # phone is writing; try again next poll
        return False, None


while True:                          # wait for a first good read
    ok, shown = read_msg()
    if ok:
        break
    time.sleep(POLL)
show(shown)

buttons = badge.Buttons()           # hold A+B for 1 s to go back to the menu
pending = None
try:
    while True:
        time.sleep(POLL)
        buttons.poll()
        ok, msg = read_msg()
        if not ok or msg == shown:
            pending = None
            continue
        if msg == pending:           # seen twice in a row -> stable
            shown, pending = msg, None
            show(msg)
        else:
            pending = msg
except KeyboardInterrupt:
    print("stopped.")

badge.exit_to_menu()                 # no-op unless launched from the menu
