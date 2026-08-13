import serial, sys, time

# usage: moncap.py [port] [seconds] [passive]
#   passive -> do NOT send Ctrl-C/Ctrl-D. CircuitPython auto-reload already
#   restarts code.py when it is copied over, so forcing a reboot on top of that
#   starts the script twice and interrupts whatever is on screen.
port = sys.argv[1] if len(sys.argv) > 1 else '/dev/cu.usbmodem101'
dur = float(sys.argv[2]) if len(sys.argv) > 2 else 18.0
passive = len(sys.argv) > 3 and sys.argv[3] == 'passive'

p = serial.Serial(port, 115200, timeout=0.2)
time.sleep(0.3)
p.reset_input_buffer()
if not passive:
    p.write(b'\x03\x03')      # Ctrl-C: break any running loop
    time.sleep(0.5)
    p.write(b'\x04')          # Ctrl-D: soft reboot -> run code.py from the top
deadline = time.time() + dur
while time.time() < deadline:
    data = p.read(4096)
    if data:
        sys.stdout.write(data.decode('utf-8', 'replace'))
        sys.stdout.flush()
p.close()
