"""Controls norns from the MK3 while the Norns Bridge plug-in page is open.

Right side: display buttons 5-7 are K1-K3 and encoders 5-7 are E1-E3.
Left side: encoders 1-4 change the running script's PARAMS, four per page, and the
page buttons step through them. The page before the first one loads a script with
one of its PSETs; display button 8 does the loading.

SHIFT keeps Machinista's own functions, and every other control and page is untouched.
The norns side of this is the "machinista" mod, which answers on OSC.
"""
import os
import socket
import struct
import time
from pathlib import Path

URI = 'https://github.com/snassen/machinista-norns#bridge'
KEYS = {'DISPLAY 5': 1, 'DISPLAY 6': 2, 'DISPLAY 7': 3}
ENCODERS = {5: 1, 6: 2, 7: 3}
PARAM_ENCODERS = (1, 2, 3, 4)
TRAVEL_FILE = Path.home() / 'norns-stack/run/encoder-travel'
DEFAULT_TRAVEL = 8  # raw encoder units per norns tick, the same as a Machinista parameter step
CODE = Path.home() / 'dust/code'
DATA = Path.home() / 'dust/data'
REQUEST_INTERVAL = 0.1
STALE_AFTER = 1.5


def osc(path, *args):
    """Encodes an OSC message with int and string arguments."""
    def pad(data):
        return data + b'\0' * (4 - len(data) % 4)
    tags = ',' + ''.join('i' if isinstance(v, int) else 's' for v in args)
    body = b''.join(struct.pack('>i', v) if isinstance(v, int) else pad(str(v).encode()) for v in args)
    return pad(path.encode()) + pad(tags.encode()) + body


def parse(data):
    """Decodes an OSC message into (path, args); raises ValueError on anything malformed."""
    def string(at):
        end = data.index(b'\0', at)
        return data[at:end].decode('utf-8', 'replace'), (end // 4 + 1) * 4
    path, at = string(0)
    tags, at = string(at)
    if not tags.startswith(','):
        raise ValueError('no type tags')
    args = []
    for tag in tags[1:]:
        if tag == 'i':
            args.append(struct.unpack_from('>i', data, at)[0]); at += 4
        elif tag == 'f':
            args.append(struct.unpack_from('>f', data, at)[0]); at += 4
        elif tag == 's':
            value, at = string(at); args.append(value)
        else:
            raise ValueError('unsupported type ' + tag)
    return path, args


def message(path, n, value):
    return osc(path, n, value)


def active(app):
    if app.page != 'PLUGIN' or app.shift:
        return False
    selected = app.plugin_selected()
    return bool(selected) and selected.get('uri') == URI


def data_name(file):
    """The data folder norns uses for code/<file>: 'awake/awake.lua' -> 'awake'."""
    folder, _, script = file.rpartition('/')
    script = script[:-4] if script.endswith('.lua') else script
    return folder if folder.endswith(script) else folder + '/' + script


SKIPPED_FOLDERS = {'lib', 'data', 'crow', 'test', 'docs'}  # the same ones the SELECT menu skips


def scripts(code=None):
    """Every script the norns SELECT menu would show, as (label, file relative to code)."""
    code = CODE if code is None else code
    found = []
    try:
        folders = [d for d in code.iterdir() if d.is_dir() and not d.name.startswith('.')]
    except OSError:
        return []
    for folder in folders:
        for root, dirs, files in os.walk(folder):
            dirs[:] = [d for d in dirs if d not in SKIPPED_FOLDERS and not d.startswith('.')]
            for name in files:
                if not name.endswith('.lua'):
                    continue
                file = os.path.relpath(os.path.join(root, name), code).replace(os.sep, '/')
                label = file[:-4]
                parts = label.split('/')
                if len(parts) == 2 and parts[0] == parts[1]:
                    label = parts[0]
                found.append((label, file))
    return sorted(found, key=lambda v: v[0].casefold())


def psets(file, data=None):
    """(number, label) for each saved PSET of a script, after 0 for 'none'."""
    data = DATA if data is None else data
    stem = Path(file).stem
    found = [(0, 'NONE')]
    for path in sorted((data / data_name(file)).glob(stem + '-[0-9][0-9].pset')):
        number = int(path.stem[-2:])
        label = f'{number:02}'
        try:
            with path.open() as handle:
                first = handle.readline().strip()
            if first.startswith('--') and first[2:].strip():
                label += ' ' + first[2:].strip()
        except OSError:
            continue
        found.append((number, label))
    return found


class NornsInput:
    def __init__(self, address=('127.0.0.1', 10111), travel_file=TRAVEL_FILE):
        self.address = address
        self.travel_file = travel_file
        self.socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.socket.bind(('127.0.0.1', 0))  # norns answers to wherever the request came from
        self.socket.setblocking(False)
        self.held = {}
        self.remainders = {}
        self.travel = DEFAULT_TRAVEL
        self.travel_checked = 0
        self.page = 1  # 0 is the load page, 1.. are PARAMS pages
        self.info = None  # the last page norns described
        self.replied = 0
        self.requested = 0
        self.script_list = []
        self.script_index = 0
        self.pset_list = [(0, 'NONE')]
        self.pset_index = 0

    def send(self, path, *args):
        try:
            self.socket.sendto(osc(path, *args), self.address)
        except OSError:
            pass  # norns not running; the MK3 must keep working regardless

    # --- answers from norns ------------------------------------------------------------
    def poll(self, now=None):
        now = time.monotonic() if now is None else now
        while True:
            try:
                data = self.socket.recv(8192)
            except (BlockingIOError, OSError):
                break
            try:
                path, args = parse(data)
            except (ValueError, IndexError, struct.error):
                continue
            if path == '/machinista/page' and args and isinstance(args[0], str):
                self.receive(args[0], now)
        if self.page >= 1 and now - self.requested >= REQUEST_INTERVAL:
            self.requested = now
            self.send('/machinista/page', self.page)

    def receive(self, payload, now):
        lines = payload.split('\n')
        head = lines[0].split('\t')
        if len(head) < 4:
            return
        try:
            page, pages = int(head[1]), int(head[2])
        except ValueError:
            return
        items = []
        for line in lines[1:5]:
            parts = line.split('\t')
            if len(parts) == 4:
                try:
                    fraction = float(parts[3])
                except ValueError:
                    fraction = -1.0
                items.append((parts[0], parts[1], parts[2], fraction))
        self.info = dict(script=head[0], page=page, pages=pages, section=head[3], items=tuple(items))
        self.replied = now
        if self.page > pages >= 1:
            self.page = pages  # the script changed and has fewer pages now

    def fresh(self, now=None):
        now = time.monotonic() if now is None else now
        return self.info is not None and now - self.replied < STALE_AFTER

    # --- the load page -------------------------------------------------------------------
    def open_load_page(self):
        self.script_list = scripts()
        current = self.info['script'] if self.info else None
        files = [data_name(file) for _, file in self.script_list]
        self.script_index = files.index(current) if current in files else 0
        self.choose_script(0)

    def choose_script(self, delta):
        if not self.script_list:
            return
        self.script_index = max(0, min(len(self.script_list) - 1, self.script_index + delta))
        self.pset_list = psets(self.script_list[self.script_index][1])
        self.pset_index = 0

    def load(self):
        if not self.script_list:
            return
        file = self.script_list[self.script_index][1]
        number = self.pset_list[self.pset_index][0]
        self.send('/machinista/load', file, number)
        self.page = 1
        self.info = None

    # --- MK3 events ----------------------------------------------------------------------
    def button(self, app, name, pressed):
        """Returns True when the event belonged to norns."""
        if not pressed:
            key = self.held.pop(name, None)
            if key is None:
                return False
            if isinstance(key, int):
                self.send('/remote/key', key, 0)  # always released, even after leaving the page
            return True
        if name in self.held:
            return True
        if not active(app):
            return False
        if name in KEYS:
            self.held[name] = KEYS[name]
            self.send('/remote/key', KEYS[name], 1)
            return True
        if name == 'DISPLAY 8' and self.page == 0:
            self.held[name] = 'LOAD'
            self.load()
            return True
        if name in ('PAGE LEFT', 'PAGE RIGHT'):
            self.held[name] = 'PAGE'
            if name == 'PAGE LEFT' and self.page > 0:
                self.page -= 1
                if self.page == 0:
                    self.open_load_page()
            elif name == 'PAGE RIGHT':
                pages = self.info['pages'] if self.info else 1
                if self.page < max(1, pages):
                    self.page += 1
            self.requested = 0
            return True
        return False

    def current_travel(self):
        now = time.monotonic()
        if now - self.travel_checked >= 1:
            self.travel_checked = now
            try:
                self.travel = max(1, int(self.travel_file.read_text().strip()))
            except (OSError, ValueError):
                self.travel = DEFAULT_TRAVEL
        return self.travel

    def steps(self, index, delta):
        remainder = self.remainders.get(index, 0)
        if remainder * delta < 0:
            remainder = 0  # reversing must respond immediately
        total = remainder + delta
        travel = self.current_travel()
        steps = (abs(total) // travel) * (1 if total >= 0 else -1)
        self.remainders[index] = total - steps * travel
        return steps

    def encoder(self, app, index, delta):
        """Returns True when the event belonged to norns."""
        if (index not in ENCODERS and index not in PARAM_ENCODERS) or not active(app):
            self.remainders.clear()
            return False
        steps = self.steps(index, delta)
        if not steps:
            return True
        if index in ENCODERS:
            self.send('/remote/enc', ENCODERS[index], steps)
        elif self.page == 0:
            if index == 1:
                self.choose_script(steps)
            elif index == 2:
                self.pset_index = max(0, min(len(self.pset_list) - 1, self.pset_index + steps))
        elif self.info and self.info['page'] == self.page and index <= len(self.info['items']):
            self.send('/machinista/delta', self.info['items'][index - 1][0], steps, self.page)
            self.requested = time.monotonic()
        return True

    # --- what the MK3 shows ----------------------------------------------------------------
    def softkeys(self, app, keys):
        if not active(app):
            return keys
        keys = list(keys)
        keys[4:7] = ['K1', 'K2', 'K3']
        if self.page == 0:
            keys[7] = 'LOAD'
        return tuple(keys)

    def decorate(self, state, app):
        state['norns_screen'] = True
        if not active(app):
            return
        self.poll()
        fields = list(state.get('fields', ()) or (None,) * 8)
        fields += [None] * (8 - len(fields))
        if self.page == 0:
            script = self.script_list[self.script_index][0] if self.script_list else 'NO SCRIPTS'
            pset = self.pset_list[self.pset_index][1]
            rows = (('SCRIPT', script, -1.0), ('PSET', pset, -1.0))
            panel = dict(title='LOAD SCRIPT', page=0, pages=self.info['pages'] if self.info else 0,
                         rows=rows, hint='BUTTON 8 LOADS IT', status='')
            fields[0:4] = [('SCRIPT', script), ('PSET', pset), None, None]
        elif self.fresh():
            info = self.info
            same = info['page'] == self.page
            rows = tuple((name, value, fraction) for _, name, value, fraction in info['items']) if same else ()
            panel = dict(title=info['section'].upper() or info['script'].upper(), page=self.page,
                         pages=info['pages'], rows=rows, hint='', status='' if rows or same else '...')
            fields[0:4] = [(name, value) for name, value, _ in rows] + [None] * (4 - len(rows))
        else:
            panel = dict(title='NORNS', page=self.page, pages=0, rows=(), hint='',
                         status='NORNS IS NOT ANSWERING')
            fields[0:4] = [None] * 4
        state['fields'] = tuple(fields)
        state['norns_panel'] = panel


bridge = NornsInput()


def decorate(state, app):
    bridge.decorate(state, app)


def softkeys(app, keys):
    return bridge.softkeys(app, keys)
