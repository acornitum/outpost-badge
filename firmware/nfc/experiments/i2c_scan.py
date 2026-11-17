# Step 1 of NFC bring-up: does anything answer on the NFC I2C bus?
# ST25DV04K expected at 0x53 (user memory + dynamic registers) and 0x57 (system config).
import board, busio, time, digitalio

SDA, SCL, GPO = board.GP10, board.GP11, board.GP12

i2c = busio.I2C(SCL, SDA, frequency=100_000)
while not i2c.try_lock():
    pass
try:
    found = i2c.scan()
finally:
    i2c.unlock()
print("i2c scan:", [hex(a) for a in found])
print("expect 0x53 and 0x57 for ST25DV04K")

gpo = digitalio.DigitalInOut(GPO)
gpo.switch_to_input(pull=digitalio.Pull.UP)
print("GPO level:", gpo.value)
while True:
    time.sleep(1)
