# Who is at 0x2d on the NFC I2C bus?
# The netlist says the only I2C part on GP10/GP11 is the ST25DV04K (0x53/0x57),
# yet a scan also finds 0x2d. This script tries to tell a real device from a
# scan artifact: is it stable, does it ACK reads and writes, does it return data
# that looks like registers, and does it follow the ST25DV in any way.
import board, busio, time

def scan(i2c):
    while not i2c.try_lock():
        pass
    try:
        return i2c.scan()
    finally:
        i2c.unlock()

def tryit(i2c, label, fn):
    while not i2c.try_lock():
        pass
    try:
        try:
            r = fn()
            print("  %-34s OK  %s" % (label, r))
        except OSError as e:
            print("  %-34s NACK/err %r" % (label, e))
    finally:
        i2c.unlock()

print("\n=== i2c probe ===")
for freq in (100_000, 400_000):
    i2c = busio.I2C(board.GP11, board.GP10, frequency=freq)
    runs = [tuple(scan(i2c)) for _ in range(10)]
    print("%d Hz: 10 scans ->" % freq, sorted(set(runs)))
    for a in (0x53, 0x57, 0x2D):
        buf = bytearray(4)
        tryit(i2c, "0x%02X readfrom(4)" % a, lambda: (i2c.readfrom_into(a, buf), bytes(buf))[1])
        buf2 = bytearray(4)
        tryit(i2c, "0x%02X write [0,0] then read 4" % a,
              lambda: (i2c.writeto_then_readfrom(a, bytes([0, 0]), buf2), bytes(buf2))[1])
        buf3 = bytearray(4)
        tryit(i2c, "0x%02X write [0] then read 4" % a,
              lambda: (i2c.writeto_then_readfrom(a, bytes([0]), buf3), bytes(buf3))[1])
        tryit(i2c, "0x%02X empty write (ack poll)" % a, lambda: i2c.writeto(a, b""))
    i2c.deinit()

# Sweep every address with an empty write and with a 1-byte read, to see
# whether 0x2d is one of a cluster (bus glitch / address aliasing).
i2c = busio.I2C(board.GP11, board.GP10, frequency=100_000)
hits_w, hits_r = [], []
for a in range(0x08, 0x78):
    while not i2c.try_lock():
        pass
    try:
        try:
            i2c.writeto(a, b"")
            hits_w.append(a)
        except OSError:
            pass
        try:
            i2c.readfrom_into(a, bytearray(1))
            hits_r.append(a)
        except OSError:
            pass
    finally:
        i2c.unlock()
print("empty-write ACK:", [hex(a) for a in hits_w])
print("1-byte-read ACK:", [hex(a) for a in hits_r])
i2c.deinit()
print("done")
while True:
    time.sleep(1)
