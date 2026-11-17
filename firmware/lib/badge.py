"""Shared badge helpers: buttons, the exit-to-menu combo, and app launching.

Buttons are switch-to-ground (internal pull-up, pressed = low), see
firmware/README.md. Hold A + B together for EXIT_HOLD seconds to leave an app:
that raises KeyboardInterrupt, which every app already treats as "stop and tidy
up" (its `finally` lowers the panel supply and cleans the screen). The app then
calls exit_to_menu().

Exit is a KeyboardInterrupt on purpose. It reuses the clean-up path the
README's rule 3 already requires, instead of adding a second one.
"""
import os
import time
import board
import digitalio
import supervisor

PINS = (("up", board.GP7), ("left", board.GP8), ("right", board.GP6),
        ("down", board.GP9), ("a", board.GP5), ("b", board.GP4))
EXIT_HOLD = 1.0       # seconds A+B must be held
DEBOUNCE = 0.02       # ignore edges closer together than this


class ExitCombo:
    """Raises KeyboardInterrupt once A and B have been down for EXIT_HOLD s.
    Feed it from any polling loop, including one that owns its own pins."""

    def __init__(self):
        self.t = None

    def update(self, a_down, b_down):
        if a_down and b_down:
            now = time.monotonic()
            if self.t is None:
                self.t = now
            elif now - self.t >= EXIT_HOLD:
                raise KeyboardInterrupt
        else:
            self.t = None


class Buttons:
    """Debounced buttons with latched presses and the A+B exit combo.

    poll() is cheap and is meant to run from inside the driver's BUSY wait
    (frame_nopower(poll=buttons.poll)), where the CPU is otherwise idle."""

    def __init__(self, wait_release=True):
        self._io = {}
        self.state = {}
        self._t = {}
        self._latch = {}
        for name, pin in PINS:
            io = digitalio.DigitalInOut(pin)
            io.direction = digitalio.Direction.INPUT
            io.pull = digitalio.Pull.UP
            self._io[name] = io
            self.state[name] = False
            self._t[name] = 0.0
            self._latch[name] = False
        self.exit = ExitCombo()
        if wait_release:
            self.wait_release()

    def poll(self):
        now = time.monotonic()
        for name in self._io:
            down = not self._io[name].value
            if down != self.state[name] and now - self._t[name] >= DEBOUNCE:
                self.state[name] = down
                self._t[name] = now
                if down:                       # act on the press, not release
                    self._latch[name] = True
        self.exit.update(self.state["a"], self.state["b"])

    def pressed(self, name):
        """True once per press of `name` (consumes the latch)."""
        if self._latch[name]:
            self._latch[name] = False
            return True
        return False

    def any_pressed(self):
        hit = False
        for name in self._latch:
            if self._latch[name]:
                self._latch[name] = False
                hit = True
        return hit

    def held(self, name):
        return self.state[name]

    def clear(self):
        for name in self._latch:
            self._latch[name] = False

    def wait_release(self, timeout=1.5):
        """Wait for every button to be up, so the press that launched an app
        (or the A+B that left one) is not read again by the next program."""
        t0 = time.monotonic()
        while time.monotonic() - t0 < timeout:
            if all(io.value for io in self._io.values()):
                break
            time.sleep(0.01)
        for name in self.state:
            self.state[name] = False
            self._t[name] = time.monotonic()
        self.clear()


def launch(path):
    """Run `path` (e.g. "/apps/flappy.py") once, then come back to code.py."""
    supervisor.set_next_code_file(path)
    supervisor.reload()


def menu_installed():
    try:
        os.stat("/apps")
        return True
    except OSError:
        return False


def exit_to_menu():
    """Go back to the menu (code.py). Does nothing when the script was
    deployed on its own, so a standalone run does not just restart itself."""
    if menu_installed():
        supervisor.reload()
