"""Sends MK3 controls to norns while the Norns Bridge plug-in page is open.

Display buttons 5-7 are K1-K3 and encoders 5-7 are E1-E3. SHIFT keeps Machinista's own
functions, and every other control and page is untouched.
"""
import socket
import struct
import time
from pathlib import Path

URI = 'https://github.com/snassen/machinista-norns#bridge'
KEYS = {'DISPLAY 5': 1, 'DISPLAY 6': 2, 'DISPLAY 7': 3}
ENCODERS = {5: 1, 6: 2, 7: 3}
TRAVEL_FILE = Path.home() / 'norns-stack/run/encoder-travel'
DEFAULT_TRAVEL = 8  # raw encoder units per norns tick, the same as a Machinista parameter step


def message(path, n, value):
    padded = path.encode() + b'\0' * (4 - len(path) % 4)
    return padded + b',ii\0' + struct.pack('>ii', n, value)


def active(app):
    if app.page != 'PLUGIN' or app.shift:
        return False
    selected = app.plugin_selected()
    return bool(selected) and selected.get('uri') == URI


class NornsInput:
    def __init__(self, address=('127.0.0.1', 10111), travel_file=TRAVEL_FILE):
        self.address = address
        self.travel_file = travel_file
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.setblocking(False)
        self.held = {}
        self.remainders = {}
        self.travel = DEFAULT_TRAVEL
        self.travel_checked = 0

    def send(self, path, n, value):
        try:
            self.socket.sendto(message(path, n, value), self.address)
        except OSError:
            pass  # norns not running; the MK3 must keep working regardless

    def button(self, app, name, pressed):
        """Returns True when the event belonged to norns."""
        if not pressed:
            key = self.held.pop(name, None)
            if key is None:
                return False
            self.send('/remote/key', key, 0)  # always released, even after leaving the page
            return True
        if name in self.held:
            return True
        if name not in KEYS or not active(app):
            return False
        self.held[name] = KEYS[name]
        self.send('/remote/key', KEYS[name], 1)
        return True

    def current_travel(self):
        now = time.monotonic()
        if now - self.travel_checked >= 1:
            self.travel_checked = now
            try:
                self.travel = max(1, int(self.travel_file.read_text().strip()))
            except (OSError, ValueError):
                self.travel = DEFAULT_TRAVEL
        return self.travel

    def encoder(self, app, index, delta):
        """Returns True when the event belonged to norns."""
        if index not in ENCODERS or not active(app):
            self.remainders.clear()
            return False
        remainder = self.remainders.get(index, 0)
        if remainder * delta < 0:
            remainder = 0  # reversing must respond immediately
        total = remainder + delta
        travel = self.current_travel()
        steps = (abs(total) // travel) * (1 if total >= 0 else -1)
        self.remainders[index] = total - steps * travel
        if steps:
            self.send('/remote/enc', ENCODERS[index], steps)
        return True


bridge = NornsInput()
