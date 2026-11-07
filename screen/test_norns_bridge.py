import json
import os
import socket
import struct
import subprocess
import tempfile
import unittest
from pathlib import Path

from app.controller_inputs import ControllerInputs
from app.mk3_session import Mk3Session
from app.mk3_state import snapshot
from app.mk3_surface import Mk3Surface
from app.model import Application
from app.norns_input import URI, NornsInput, bridge


class NornsBridgeControls(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.a = Application(self.tmp.name)
        self.s = Mk3Surface(self.a, ControllerInputs(self.a))
        engine = Path(__file__).resolve().parents[1] / 'build/audio_engine'
        catalog = json.loads(subprocess.check_output([str(engine), '--plugins'], text=True, env=dict(os.environ, LV2_PATH='/var/lib/machinista/lv2/plugins')))
        self.a.plugin_catalog = catalog
        self.a.control_level = 2
        self.a.page = 'PLUGIN'
        self.a.plugin_slot = 0
        self.load(URI)
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.listener.bind(('127.0.0.1', 0))
        self.listener.settimeout(0.05)
        self.addCleanup(self.listener.close)
        bridge.address = self.listener.getsockname()
        bridge.travel_file = Path(self.tmp.name) / 'no-such-file'
        bridge.travel_checked = 0
        bridge.held.clear()
        bridge.remainders.clear()

    def load(self, uri):
        meta = next(p for p in self.a.plugin_catalog if p['uri'] == uri)
        self.a.track()['plugins'] = [{'uri': uri, 'kind': meta['kind'], 'bypass': False,
                                      'controls': {p['symbol']: p['default'] for p in meta['ports']}}]

    def sent(self):
        out = []
        while True:
            try:
                data = self.listener.recv(64)
            except socket.timeout:
                return out
            out.append((data[:data.index(b'\0')].decode(),) + struct.unpack('>ii', data[-8:]))

    def tap(self, name):
        self.s.button(name, True)
        self.s.button(name, False)

    def test_display_buttons_are_norns_keys(self):
        for name, key in (('DISPLAY 5', 1), ('DISPLAY 6', 2), ('DISPLAY 7', 3)):
            self.tap(name)
            self.assertEqual(self.sent(), [('/remote/key', key, 1), ('/remote/key', key, 0)])
            self.assertEqual(self.a.page, 'PLUGIN')

    def test_labels_and_screen_flag(self):
        keys = self.s.softkeys()
        self.assertEqual(tuple(keys[4:7]), ('K1', 'K2', 'K3'))
        self.assertTrue(snapshot(self.s).get('norns_screen'))

    def test_machinista_keeps_the_other_buttons(self):
        self.tap('DISPLAY 8')
        self.tap('DISPLAY 3')
        self.assertEqual(self.sent(), [])

    def test_shift_keeps_machinista_functions(self):
        self.a.shift = True
        self.assertNotIn('K1', self.s.softkeys())
        self.tap('DISPLAY 7')
        self.assertEqual(self.sent(), [])
        self.a.shift = False

    def test_other_pages_and_plugins_are_untouched(self):
        self.a.page = 'CHANNEL'
        self.tap('DISPLAY 5')
        self.assertEqual(self.sent(), [])
        self.a.page = 'PLUGIN'
        other = next(p['uri'] for p in self.a.plugin_catalog if p['kind'] == 'INSTRUMENT' and p['uri'] != URI and p['supported'])
        self.load(other)
        self.assertNotIn('K1', self.s.softkeys())
        self.tap('DISPLAY 7')
        self.assertEqual(self.sent(), [])

    def test_key_is_released_after_leaving_the_page(self):
        self.s.button('DISPLAY 5', True)
        self.a.page = 'CHANNEL'
        self.s.button('DISPLAY 5', False)
        self.assertEqual(self.sent(), [('/remote/key', 1, 1), ('/remote/key', 1, 0)])

    def test_encoders(self):
        session = Mk3Session.__new__(Mk3Session)
        session.app, session.surface = self.a, self.s
        session.encoder_remainders, session.encoder_context = {}, None
        session.rotate_raw(5, 4)
        self.assertEqual(self.sent(), [])
        session.rotate_raw(5, 4)
        self.assertEqual(self.sent(), [('/remote/enc', 1, 1)])
        session.rotate_raw(6, 3)
        session.rotate_raw(6, -8)  # reversing discards travel in the old direction
        self.assertEqual(self.sent(), [('/remote/enc', 2, -1)])
        session.rotate_raw(7, 24)
        self.assertEqual(self.sent(), [('/remote/enc', 3, 3)])
        before = self.a.track()['plugins'][0]['controls']['gain']
        session.rotate_raw(1, -80)  # encoder 1 stays the bridge's gain
        self.assertLess(self.a.track()['plugins'][0]['controls']['gain'], before)
        session.rotate_raw(8, 80)
        self.assertEqual(self.sent(), [])

    def test_travel_can_be_tuned_while_running(self):
        path = Path(self.tmp.name) / 'travel'
        path.write_text('16\n')
        bridge.travel_file = path
        session = Mk3Session.__new__(Mk3Session)
        session.app, session.surface = self.a, self.s
        session.encoder_remainders, session.encoder_context = {}, None
        session.rotate_raw(5, 8)
        self.assertEqual(self.sent(), [])
        session.rotate_raw(5, 8)
        self.assertEqual(self.sent(), [('/remote/enc', 1, 1)])


if __name__ == '__main__':
    unittest.main()
