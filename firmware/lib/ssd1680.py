# ssd1680.py -- minimal raw driver for the Outpost badge 2.9" e-ink panel
#
# Panel: 0290BN800F6HP-DL  (2.9", 296x128, SSD1680 controller)
# Board: Outpost badge, RP2354A
#
# Wiring (from the KiCad netlist -> RP2350 GPIO numbers):
#   CS   = GP13    SCK  = GP14 (SPI1 SCK)    MOSI = GP15 (SPI1 TX)
#   BUSY = GP16    DC   = GP17               RST  = GP18
#
# We drive the controller with raw commands (no displayio) so we can do
# fast PARTIAL refresh, which displayio.EPaperDisplay does not expose.
#
# ---------------------------------------------------------------------------
# SPEED, AND WHAT IT COSTS
#
# Measured on this panel at room temperature:
#     full refresh                    2.29  s   0.44 fps
#     stock partial refresh           0.617 s   1.6  fps
#     + charge pump held up           0.478 s   2.1  fps   <- free
#     + shortened waveform            0.107 s   9.4  fps   <- costs contrast
#
# Three things produce that. Two are free, one is not:
#   1. arm_partial() does the setup ONCE (analog power up, LUT load) and
#      frame_nopower() runs only the per-frame part. A normal driver pays a
#      ~139 ms charge-pump cycle on every frame. Free: no waveform changes.
#   2. The custom LUT shortens the drive from ~438 ms to ~40 ms. NOT free.
#      Contrast follows total panel time and nothing else changes that. At
#      ~10 ms of drive the animation still runs but no image is visible.
#   3. sync_old=True keeps the reference RAM correct. Costs ~5% speed and is
#      mandatory -- see the warning below.
#
# Ideas that measured as WORTHLESS on this panel (do not re-try them blind):
#   * driving fewer gate lines / a window -- 296 lines 0.05048 s vs 32 lines
#     0.05109 s. The LUT frame period comes from the controller oscillator and
#     does not scale with line count. A game CANNOT save time by updating a
#     small region.
#   * reusing the loaded LUT (0x22=0xC7), and registers 0x3A / 0x3B.
#
# ---------------------------------------------------------------------------
# THREE WARNINGS. The first two were real failures, not theory.
#
# 1. ALWAYS pass sync_old=True to frame_nopower().
#    Without it the panel ratchets one direction and stops responding after
#    ~80 frames. Nothing in the timing shows it: the broken version held a
#    rock-steady 18.4 fps with normal BUSY times while the panel was dead.
#    See frame_nopower() for the mechanism.
#
# 2. ALWAYS call init() before a full refresh that follows arm_partial().
#    arm_partial() leaves the custom LUT in 0x32 and overwrites the voltage
#    registers 0x03/0x04/0x2C. Nothing restores the factory table, so a plain
#    display_full() afterwards runs the SHORT waveform: measured 0.613 s
#    instead of 2.29 s, and almost invisible on the panel.
#        epd.init()              # software reset -> factory LUT from OTP
#        epd.display_full(buf)
#
# 3. ALWAYS call power_off() when you finish with the fast path, from a
#    `finally` and not an `except`.
#    A full refresh ends with 0x22=0xF7, whose low bits disable analog and
#    clock, so display_full()/display_base() power down by themselves. The fast
#    path does not: arm_partial() deliberately leaves the charge pump up and
#    only power_off() lowers it. If a program stops without calling it, the
#    panel's high-voltage supply stays energised.
#
#        try:
#            epd.arm_partial(...)
#            while True:
#                epd.frame_nopower(buf, sync_old=True)
#        finally:
#            epd.power_off()
#
#    `except KeyboardInterrupt` is NOT sufficient. CircuitPython's auto-reload
#    fires whenever a file is copied to the drive and does not raise
#    KeyboardInterrupt, and neither does an unexpected exception; both skip an
#    except-only handler.
#
#    What this costs is certain for current draw -- the supply simply stays on,
#    which matters on a battery. Whether a sustained bias also harms the panel
#    is standard e-paper guidance rather than something measured here; we did
#    not test it. Treat it as a reason to be strict, not as a known failure.
#
# Also worth knowing: display_base() is a full-strength 2.29 s refresh, so
# whatever it shows is burned in hard and leaves the strongest ghost on the
# panel. Seed it with a blank background, not with real content.
#
# Changes from a stock SSD1680 driver:
#   * arm_partial() / frame_nopower() split the setup from the per-frame work.
#   * load_partial_lut() exposes tpa, frame_rate and groups (the drive time).
#   * frame_nopower(sync_old=) writes the old RAM after each update.
#   * _wait(poll=) and frame_nopower(poll=) run a callback inside the BUSY
#     wait, so button taps shorter than one frame are not lost. Sampling there
#     costs no frame rate -- it is time the CPU already spends idle.
#
# Full write-up, with the measurements: ../epaper/README.md
# ---------------------------------------------------------------------------

import time
import digitalio

# Panel geometry. The SSD1680 RAM is organised as 128 px (16 bytes) across a
# row, 296 rows tall.
WIDTH = 128
HEIGHT = 296
_STRIDE = WIDTH // 8          # bytes per row = 16
_BUFLEN = _STRIDE * HEIGHT    # 4736 bytes

# Fast partial-refresh waveform, ported verbatim from Waveshare's field-tested
# epd2in9_V2 driver (SSD1680, 296x128). 153 LUT bytes + 6 trailing config bytes
# ([153]=0x3F border, [154]=gate V, [155:158]=source V VSH1/VSH2/VSL, [158]=VCOM).
#
# In group 0 the black->white and white->black entries are exact opposites, so
# shortening the group should scale both directions equally and leave per-pixel
# charge balanced. That is an argument from reading this table, NOT a
# measurement -- no instrument reading of DC balance was taken.
WF_PARTIAL = bytes((
    0x00, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x80, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x40, 0x40, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x80, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x0A, 0x00, 0x00, 0x00, 0x00, 0x00, 0x01,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x01, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x00, 0x00, 0x00, 0x00, 0x00, 0x00, 0x00,
    0x22, 0x22, 0x22, 0x22, 0x22, 0x22, 0x00, 0x00, 0x00,
    0x22, 0x17, 0x41, 0xB0, 0x32, 0x36,
))


def _out(pin, value):
    p = digitalio.DigitalInOut(pin)
    p.direction = digitalio.Direction.OUTPUT
    p.value = value
    return p


class SSD1680:
    def __init__(self, spi, cs, dc, rst, busy, baudrate=20_000_000):
        self.spi = spi
        self.baud = baudrate
        self.timeout = 15.0          # BUSY-wait timeout (s)
        self._mode2 = False          # is the mode-2 partial LUT currently loaded?
        self.cs = _out(cs, True)
        self.dc = _out(dc, True)
        self.rst = _out(rst, True)
        self.busy = digitalio.DigitalInOut(busy)
        self.busy.direction = digitalio.Direction.INPUT

    # ---- low level ---------------------------------------------------------
    def _write(self, data):
        while not self.spi.try_lock():
            pass
        try:
            self.spi.configure(baudrate=self.baud, phase=0, polarity=0)
            self.spi.write(data)
        finally:
            self.spi.unlock()

    def _cmd(self, command, data=None):
        self.dc.value = False        # command
        self.cs.value = False
        self._write(bytes([command]))
        self.cs.value = True
        if data is not None:
            self.dc.value = True     # data
            self.cs.value = False
            # pass buffers (incl. memoryview slices) straight through -- copying
            # a 4736-byte frame here would cost more than the SPI transfer
            if not isinstance(data, (bytes, bytearray, memoryview)):
                data = bytes(data)
            self._write(data)
            self.cs.value = True

    def _wait(self, timeout=None, poll=None):
        """Block while BUSY is high. SSD1680 BUSY = high means busy.
        Returns True if the panel went idle, False on timeout (bad sign).

        `poll` is an optional zero-argument callable run on every iteration.
        A frame blocks here for ~100 ms, so anything that must not be missed
        during that window -- a button tap shorter than one frame -- has to be
        sampled from inside this loop."""
        if timeout is None:
            timeout = self.timeout
        t0 = time.monotonic()
        while self.busy.value:          # tight poll: max timing resolution
            if poll is not None:
                poll()
            if time.monotonic() - t0 > timeout:
                return False
        return True

    def reset(self):
        self.rst.value = True
        time.sleep(0.02)
        self.rst.value = False
        time.sleep(0.005)
        self.rst.value = True
        time.sleep(0.02)
        return self._wait()

    # ---- init --------------------------------------------------------------
    def init(self):
        ok = self.reset()
        self._cmd(0x12)              # SW reset
        self._wait()
        self._cmd(0x01, [(HEIGHT - 1) & 0xFF, (HEIGHT - 1) >> 8, 0x00])  # driver output
        self._cmd(0x11, [0x03])     # data entry: X inc, Y inc
        self._set_window(0, 0, WIDTH - 1, HEIGHT - 1)
        self._cmd(0x3C, [0x05])     # border waveform
        self._cmd(0x18, [0x80])     # temperature sensor: internal
        self._cmd(0x21, [0x00, 0x80])  # display update control 1
        self._set_cursor(0, 0)
        self._wait()
        return ok

    def _set_window(self, x0, y0, x1, y1):
        self._cmd(0x44, [x0 // 8, x1 // 8])            # RAM X start/end (in bytes)
        self._cmd(0x45, [y0 & 0xFF, y0 >> 8, y1 & 0xFF, y1 >> 8])  # RAM Y start/end

    def _set_cursor(self, x, y):
        self._cmd(0x4E, [x // 8])
        self._cmd(0x4F, [y & 0xFF, y >> 8])

    # ---- refresh -----------------------------------------------------------
    def _load(self, command, buf):
        self._set_window(0, 0, WIDTH - 1, HEIGHT - 1)
        self._set_cursor(0, 0)
        self._cmd(command, buf)

    def _run(self, mode):
        """Trigger update sequence `mode` (0x22) and time the BUSY wait."""
        self._cmd(0x22, [mode])
        self._cmd(0x20)
        t0 = time.monotonic()
        ok = self._wait()
        return (time.monotonic() - t0, ok)

    def _set_lines(self, n):
        """Set the number of active gate lines (driven from gate 0)."""
        self._cmd(0x01, [(n - 1) & 0xFF, (n - 1) >> 8, 0x00])

    def set_frame_timing(self, dummy_line, gate_width):
        """Scale the whole waveform's speed via 0x3A (dummy-line period) and
        0x3B (gate-line width). Lower = faster. This changes only timing, not
        the LUT's voltage pattern, so DC balance is preserved (safe; worst case
        is cosmetic ghosting). Must be applied AFTER the LUT is loaded (i.e.
        used with fast 0xC7 refreshes, which don't reload timing from OTP)."""
        self._cmd(0x3A, [dummy_line & 0xFF])
        self._cmd(0x3B, [gate_width & 0xFF])

    def display_full(self, buf):
        """Slow, clean full refresh (~2.29 s). Returns (seconds, busy_ok).

        WARNING: call init() first if arm_partial() has run since power-up.
        This method only triggers update sequence 0xF7; it does not restore the
        factory waveform, and arm_partial() leaves the custom short LUT loaded.
        Without the init() the refresh takes 0.613 s instead of 2.29 s and is
        almost invisible:

            epd.init()
            epd.display_full(buf)
        """
        self._set_lines(HEIGHT)
        self._load(0x24, buf)
        self._mode2 = False
        return self._run(0xF7)

    def display_base(self, buf):
        """Full refresh that also seeds the partial-refresh reference RAM.

        Call this once before a run of frame_nopower() calls: partial refresh
        is differential, so the controller's 'old' RAM must match what is
        physically on the panel.

        WARNING 1: as with display_full(), call init() first if arm_partial()
        has already run, or this uses the short waveform and barely marks the
        panel.

        WARNING 2: this drives at full strength for 2.29 s, so whatever it
        shows is burned in harder than anything else and leaves the strongest
        ghost. Seed with a blank background and let the first partial update
        draw the real content -- do not seed with frame 0 of an animation.
        """
        self._set_lines(HEIGHT)
        self._load(0x24, buf)
        self._load(0x26, buf)       # seed 'old' RAM so partial has a reference
        self._mode2 = False
        return self._run(0xF7)

    def display_window(self, buf, lines, fast=True):
        """Windowed partial refresh: drive only the first `lines` gate lines (a
        band at the gate-0 edge). Refresh time scales with `lines`, so a small
        game viewport refreshes far faster. Uses the factory DC-balanced LUT, so
        it's safe for the panel. `buf` is a full-height framebuffer; only its
        first `lines` rows are sent. Returns (seconds, busy_ok)."""
        self._set_lines(lines)
        self._cmd(0x3C, [0x80])
        self._cmd(0x11, [0x03])
        self._cmd(0x44, [0, WIDTH // 8 - 1])
        self._cmd(0x45, [0, 0, (lines - 1) & 0xFF, (lines - 1) >> 8])
        self._cmd(0x4E, [0])
        self._cmd(0x4F, [0, 0])
        self._cmd(0x24, buf[0:lines * _STRIDE])
        if fast and self._mode2:
            return self._run(0xC7)
        self._mode2 = True
        return self._run(0xFF)

    def display_partial(self, buf, fast=True):
        """Partial refresh. The first call after a full/base refresh loads the
        mode-2 partial LUT (0x22=0xFF, ~0.6 s). When fast=True, subsequent calls
        reuse that already-loaded LUT (0x22=0xC7) and skip the per-frame
        temperature + LUT reload, which roughly halves the refresh time. The
        waveform is still the factory DC-balanced LUT -- safe for the panel.
        Returns (seconds, busy_ok)."""
        self._cmd(0x3C, [0x80])     # partial border waveform
        self._load(0x24, buf)
        if fast and self._mode2:
            return self._run(0xC7)  # display only, reuse loaded LUT
        self._mode2 = True
        return self._run(0xFF)      # load temp + mode-2 LUT + display

    # ---- custom-LUT fast partial (Waveshare WF_PARTIAL) --------------------
    def _load_lut(self, lut):
        self._cmd(0x32, lut[0:153])
        self._cmd(0x3F, [lut[153]])
        self._cmd(0x03, [lut[154]])              # gate voltage
        self._cmd(0x04, [lut[155], lut[156], lut[157]])  # source VSH1/VSH2/VSL
        self._cmd(0x2C, [lut[158]])              # VCOM
        self._wait()

    def display_fast(self, buf, dummy=None, gate=None):
        """One-shot fast partial refresh, ported exactly from Waveshare's
        display_Partial (custom LUT + 0x22=0x0F). Reloads the LUT each call, so
        it's self-contained but has per-frame overhead. Optional dummy/gate set
        the frame-timing registers (0x3A/0x3B) to try to speed the custom
        waveform. Returns (seconds, ok)."""
        self.rst.value = False
        time.sleep(0.002)
        self.rst.value = True
        time.sleep(0.002)
        self._load_lut(WF_PARTIAL)
        if dummy is not None:
            self._cmd(0x3A, [dummy & 0xFF])
            self._cmd(0x3B, [gate & 0xFF])
        self._cmd(0x37, [0, 0, 0, 0, 0, 0x40, 0, 0, 0, 0])
        self._cmd(0x3C, [0x80])
        self._cmd(0x22, [0xC0])
        self._cmd(0x20)
        self._wait()
        self._set_window(0, 0, WIDTH - 1, HEIGHT - 1)
        self._set_cursor(0, 0)
        self._cmd(0x24, buf)
        return self._run(0x0F)

    def enter_fast(self):
        """Set up custom-LUT fast partial ONCE (reset + load LUT + arm), so the
        per-frame path can skip that overhead. Call after display_base()."""
        self.rst.value = False
        time.sleep(0.002)
        self.rst.value = True
        time.sleep(0.002)
        self._set_lines(HEIGHT)
        self._cmd(0x11, [0x03])
        self._load_lut(WF_PARTIAL)
        self._cmd(0x37, [0, 0, 0, 0, 0, 0x40, 0, 0, 0, 0])
        self._cmd(0x3C, [0x80])
        self._cmd(0x22, [0xC0])
        self._cmd(0x20)
        self._wait()

    def frame_fast(self, buf):
        """Per-frame fast partial after enter_fast(): just load RAM + display.
        This is the game loop path. Returns (seconds, ok)."""
        self._set_window(0, 0, WIDTH - 1, HEIGHT - 1)
        self._set_cursor(0, 0)
        self._cmd(0x24, buf)
        return self._run(0x0F)

    # ---- keep-the-charge-pump-on fast path --------------------------------
    # Every stock update mode (0xF7/0xFF/0xC7, and Waveshare's 0xC0+0x0F) powers
    # the DC-DC charge pump UP and back DOWN on every frame. That ramp is fixed
    # cost and does not depend on the waveform. power_on() does it once;
    # frame_nopower() then issues display-only updates (0x22=0x04), which touch
    # neither the LUT nor the rail voltages -> DC balance is unchanged (safe).
    def power_on(self):
        self._cmd(0x22, [0xC0])     # enable clock + analog, no display
        self._cmd(0x20)
        return self._wait()

    def power_off(self):
        """Lower the charge pump: disable the analog supply and the clock.

        Call this whenever you finish a run of frame_nopower() calls, and put
        it in a `finally` so a reload or an unexpected exception cannot skip it
        (see warning 3 at the top of this file). arm_partial() leaves the pump
        up on purpose -- that is where the free speed comes from -- so nothing
        else lowers it. A full refresh powers down inside its own sequence, so
        it does not need this.

        For the lowest power when the panel will sit idle for a long time,
        follow this with sleep()."""
        self._cmd(0x22, [0x03])     # disable analog + clock
        self._cmd(0x20)
        return self._wait()

    def frame_nopower(self, buf, mode=0x0C, window=None, sync_old=True,
                      poll=None):
        """Display-only refresh with the charge pump already up.

        `mode` 0x0C = display with DISPLAY Mode 2 (the differential/partial
        mode); 0x04 is Mode 1 and is NOT correct for partial updates.

        sync_old=True copies the frame into the 'old' RAM (0x26) after the
        update. This is REQUIRED for safety, not an optimization: the controller
        chooses each pixel's waveform by comparing old RAM against new RAM. If
        old RAM goes stale, unchanged pixels are treated as changing and get
        driven the same direction every frame -- charge accumulates one way, the
        panel darkens toward black, and the DC balance the LUT normally
        guarantees is lost. Keeping it in sync means unchanged pixels select
        LUT0/LUT3, which are VSS (no drive at all).

        Optional `window`=(y0,y1) sends only those rows.
        Returns (write_s, busy_s, ok)."""
        t0 = time.monotonic()
        if window is None:
            self._set_window(0, 0, WIDTH - 1, HEIGHT - 1)
            self._set_cursor(0, 0)
            self._cmd(0x24, buf)
        else:
            y0, y1 = window
            self._cmd(0x44, [0, WIDTH // 8 - 1])
            self._cmd(0x45, [y0 & 0xFF, y0 >> 8, y1 & 0xFF, y1 >> 8])
            self._cmd(0x4E, [0])
            self._cmd(0x4F, [y0 & 0xFF, y0 >> 8])
            self._cmd(0x24, buf[y0 * _STRIDE:(y1 + 1) * _STRIDE])
        tw = time.monotonic() - t0
        self._cmd(0x22, [mode])
        self._cmd(0x20)
        t1 = time.monotonic()
        ok = self._wait(poll=poll)
        tb = time.monotonic() - t1
        if sync_old:
            # old RAM <- what is now on screen, so the next frame's differential
            # is correct and static pixels stay undriven
            if window is None:
                self._set_window(0, 0, WIDTH - 1, HEIGHT - 1)
                self._set_cursor(0, 0)
                self._cmd(0x26, buf)
            else:
                y0, y1 = window
                self._cmd(0x4F, [y0 & 0xFF, y0 >> 8])
                self._cmd(0x26, buf[y0 * _STRIDE:(y1 + 1) * _STRIDE])
        return (tw, tb, ok)

    # WF_PARTIAL layout: [0:60] voltage patterns (5 LUTs x 12 groups, 4 phases
    # per byte), [60:144] phase timing (12 groups x 7 bytes: TPA,TPB,TPC,TPD,
    # SRAB,SRCD,RP), [144:150] frame rates, [150:153] pad, then the 6 trailing
    # config bytes. Group 0 (TPA at byte 60) runs 10 frames and dominates.
    _TPA0 = 60          # group 0 phase-A frame count
    _FRAME_RATE = 144   # first of six frame-rate bytes

    def load_partial_lut(self, lut=None, tpa=None, frame_rate=None, groups=None):
        """Load the custom partial LUT, optionally shortening group 0's drive
        (`tpa`, default 10 frames) and/or overriding the frame-rate bytes.

        Safety: in group 0 the black->white and white->black entries are VSL and
        VSH1 -- exact opposites -- while unchanged pixels sit at VSS (no drive).
        Shortening group 0 therefore scales the positive and negative drive
        equally, so per-pixel charge stays balanced. The cost is under-driving
        (weaker contrast / ghosting), which is cosmetic and cleared by a full
        refresh -- not the irreversible DC-imbalance failure mode."""
        lut = bytearray(WF_PARTIAL if lut is None else lut)
        if tpa is not None:
            lut[self._TPA0] = tpa & 0xFF
        if frame_rate is not None:
            for i in range(self._FRAME_RATE, self._FRAME_RATE + 6):
                lut[i] = frame_rate
        if groups is not None:
            # groups = number of phase groups to keep (1..3). Group 1 is a hold
            # pulse that biases unchanged pixels (VSH1 on static black, VSL on
            # static white) -- dropping it saves a frame AND removes the bias
            # that makes periodic full refreshes necessary.
            if groups < 3:
                lut[self._TPA0 + 14] = 0    # group 2 TPA
            if groups < 2:
                lut[self._TPA0 + 7] = 0     # group 1 TPA
        self._load_lut(lut)

    def arm_partial(self, tpa=None, frame_rate=None, groups=None, lines=None):
        """Reset, load the (optionally shortened) partial LUT, and bring the
        charge pump up -- everything the per-frame path shouldn't repeat.
        After this, call frame_nopower() per frame, then power_off().

        Holding the charge pump up across frames is the one large free win:
        0.617 s -> 0.478 s with no change to any voltage or waveform. The cost
        is that the analog supply stays on, so call power_off() when you stop.

        `tpa`, `frame_rate` and `groups` set the drive time, which is the
        contrast/speed trade -- see load_partial_lut(). Known-good setting,
        confirmed by eye at 9.4 fps:

            epd.arm_partial(tpa=2, frame_rate=0x44, groups=None)

        WARNING: this leaves the custom LUT in register 0x32 and overwrites the
        voltage registers 0x03/0x04/0x2C. They stay loaded until init() does a
        software reset. Any full refresh in between runs the short waveform and
        is almost invisible.

        `lines` sets the gate-line count. Note that 0x01 is latched when the
        analog powers up, so it MUST be set here rather than after power_on() --
        an earlier version set it afterwards and the register did nothing.
        Reducing it saves no time (296 lines 0.05048 s, 32 lines 0.05109 s); it
        is exposed only because getting the ordering right was needed to prove
        that.
        """
        self.rst.value = False
        time.sleep(0.002)
        self.rst.value = True
        time.sleep(0.002)
        # 0x01 is latched when the analog powers up, so the gate-line count MUST
        # be set here, before power_on() -- setting it afterwards does nothing.
        self._set_lines(HEIGHT if lines is None else lines)
        self._cmd(0x11, [0x03])
        self.load_partial_lut(tpa=tpa, frame_rate=frame_rate, groups=groups)
        self._cmd(0x37, [0, 0, 0, 0, 0, 0x40, 0, 0, 0, 0])
        self._cmd(0x3C, [0x80])
        return self.power_on()

    def sleep(self):
        """Deep sleep -- the lowest-power state.

        Use it after power_off() when the panel will be idle for a long time.
        The image stays on the panel; e-paper needs no power to hold it.

        NOTE: waking from deep sleep needs a hardware reset. init() does one,
        so call init() before using the panel again. Untested on this board --
        nothing in the firmware calls it yet."""
        self._cmd(0x10, [0x01])     # deep sleep mode 1


# --------------------------------------------------------------------------
# Tiny 1-bit framebuffer.  Convention: hardware RAM bit 1 = white, 0 = black,
# so a blank page is all 0xFF.  Drawing "black" clears bits.
# --------------------------------------------------------------------------
class FrameBuffer:
    def __init__(self, width=WIDTH, height=HEIGHT):
        self.w = width
        self.h = height
        self.stride = width // 8
        self.buf = bytearray(b"\xFF" * (self.stride * height))

    def fill(self, black=False):
        self.buf[:] = b"\x00" * len(self.buf) if black else b"\xFF" * len(self.buf)

    def pixel(self, x, y, black=True):
        if x < 0 or x >= self.w or y < 0 or y >= self.h:
            return
        i = y * self.stride + (x >> 3)
        m = 0x80 >> (x & 7)
        if black:
            self.buf[i] &= ~m & 0xFF
        else:
            self.buf[i] |= m

    def rect(self, x, y, w, h, black=True, fill=True):
        if fill:
            for yy in range(y, y + h):
                for xx in range(x, x + w):
                    self.pixel(xx, yy, black)
        else:
            for xx in range(x, x + w):
                self.pixel(xx, y, black)
                self.pixel(xx, y + h - 1, black)
            for yy in range(y, y + h):
                self.pixel(x, yy, black)
                self.pixel(x + w - 1, yy, black)

    def hline(self, x, y, w, black=True):
        for xx in range(x, x + w):
            self.pixel(xx, y, black)

    def vline(self, x, y, h, black=True):
        for yy in range(y, y + h):
            self.pixel(x, yy, black)

    # ---- 7-segment digit rendering (no font file needed) -------------------
    # segment order: a b c d e f g
    _SEGMENTS = {
        0: "abcdef", 1: "bc", 2: "abged", 3: "abgcd", 4: "fgbc",
        5: "afgcd", 6: "afgecd", 7: "abc", 8: "abcdefg", 9: "abcdfg",
    }

    def digit(self, x, y, value, t=6, l=24, black=True):
        """Draw one 7-seg digit at (x,y). t=thickness, l=segment length.
        Cell size = (l+2t) wide by (3t+2l) tall."""
        segs = self._SEGMENTS.get(value, "")
        L = l
        pos = {
            "a": (x + t, y, L, t),
            "f": (x, y + t, t, L),
            "b": (x + t + L, y + t, t, L),
            "g": (x + t, y + t + L, L, t),
            "e": (x, y + 2 * t + L, t, L),
            "c": (x + t + L, y + 2 * t + L, t, L),
            "d": (x + t, y + 2 * t + 2 * L, L, t),
        }
        for s in segs:
            rx, ry, rw, rh = pos[s]
            self.rect(rx, ry, rw, rh, black=black, fill=True)

    def number(self, x, y, value, t=6, l=24, gap=8, black=True):
        cell = l + 2 * t
        s = str(value)
        for i, ch in enumerate(s):
            self.digit(x + i * (cell + gap), y, int(ch), t=t, l=l, black=black)
        return len(s) * (cell + gap) - gap
