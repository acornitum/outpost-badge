# Step 2 of NFC bring-up: identify the chip, read registers, dump user memory,
# then write a URL NDEF record and read it back over I2C.
# After it prints PASS, tap a phone on the badge: it should open the URL.
import board, busio, time
from st25dv import ST25DV, uri_record

URL = "https://hackclub.com"

i2c = busio.I2C(board.GP11, board.GP10, frequency=100_000)
nfc = ST25DV(i2c)

print("UID      :", nfc.uid().hex())
print("IC_REF   : 0x%02X (ST25DV04K should be 0x24)" % nfc.ic_ref())
print("mem bytes:", nfc.mem_bytes())
for name, reg in (("GPO_CTRL", 0x2000), ("EH_CTRL", 0x2002), ("RF_MNGT", 0x2003),
                  ("I2C_SSO", 0x2004), ("IT_STS", 0x2005), ("MB_CTRL", 0x2006)):
    print("%-9s: 0x%02X" % (name, nfc.read(reg, 1)[0]))
print("user mem[0:32] before:", nfc.read(0, 32).hex())

msg = uri_record(URL)
nfc.write_ndef(msg)
back = nfc.read_ndef()
print("user mem[0:32] after :", nfc.read(0, 32).hex())
print("NDEF wrote:", msg.hex())
print("NDEF read :", back.hex() if back is not None else None)
print("PASS" if back == msg else "FAIL")
print("now tap a phone on the badge")
while True:
    time.sleep(1)
