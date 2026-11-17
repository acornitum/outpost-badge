# Is 0x2d produced by the RP2350's hardware I2C block, or really on the wire?
# Scan the same two pins with software (bit-banged) I2C, which does not use the
# hardware block, and at a very slow clock.
import board, time
try:
    import bitbangio
except ImportError:
    bitbangio = None
import busio

print("\n=== i2c bitbang ===")
if bitbangio is None:
    print("bitbangio not available in this build")
else:
    for freq in (100_000, 10_000):
        i2c = bitbangio.I2C(board.GP11, board.GP10, frequency=freq)
        while not i2c.try_lock():
            pass
        print("bitbang %6d Hz:" % freq, [hex(a) for a in i2c.scan()])
        buf = bytearray(2)
        for a in (0x2D, 0x53):
            try:
                i2c.readfrom_into(a, buf)
                print("  read 0x%02X ->" % a, bytes(buf))
            except OSError as e:
                print("  read 0x%02X -> %r" % (a, e))
        i2c.unlock()
        i2c.deinit()
    i2c = busio.I2C(board.GP11, board.GP10, frequency=10_000)
    while not i2c.try_lock():
        pass
    print("hardware 10000 Hz:", [hex(a) for a in i2c.scan()])
    i2c.unlock()
    i2c.deinit()
print("done")
while True:
    time.sleep(1)
