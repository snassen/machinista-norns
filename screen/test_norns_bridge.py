import json
import os
import socket
import subprocess
import tempfile
import time
import unittest
from pathlib import Path

from app.controller_inputs import ControllerInputs
from app.mk3_session import Mk3Session
from app.mk3_state import snapshot
from app.mk3_surface import Mk3Surface
from app.model import Application
import app.norns_input as norns_input
from app.norns_input import URI, bridge, osc, parse, data_name, psets, scripts

PAYLOAD = ('awake\t1\t3\tSTEP\n'
           'step_div\tstep division\t1/4\t0.5000\n'
           'note_length\tnote length\t50%\t0.2500\n'
           'scale_mode\tscale mode\tdorian\t0.1000\n'
           'root_note\troot note\tC3\t-1')


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
        # A fake norns: receives what the bridge sends, and can answer like the mod.
        self.norns = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.norns.bind(('127.0.0.1', 0))
        self.norns.settimeout(0.05)
        self.addCleanup(self.norns.close)
        bridge.address = self.norns.getsockname()
        bridge.travel_file = Path(self.tmp.name) / 'no-such-file'
        bridge.travel_checked = 0
        bridge.held.clear()
        bridge.remainders.clear()
        bridge.page, bridge.info, bridge.replied, bridge.requested = 1, None, 0, time.monotonic() + 3600
        self.dust = Path(self.tmp.name) / 'dust'
        self.addCleanup(setattr, norns_input, 'CODE', norns_input.CODE)
        self.addCleanup(setattr, norns_input, 'DATA', norns_input.DATA)
        norns_input.CODE, norns_input.DATA = self.dust / 'code', self.dust / 'data'

    def load(self, uri):
        meta = next(p for p in self.a.plugin_catalog if p['uri'] == uri)
        self.a.track()['plugins'] = [{'uri': uri, 'kind': meta['kind'], 'bypass': False,
                                      'controls': {p['symbol']: p['default'] for p in meta['ports']}}]

    def sent(self):
        out = []
        while True:
            try:
                data, self.reply_to = self.norns.recvfrom(8192)
            except socket.timeout:
                return out
            path, args = parse(data)
            out.append((path,) + tuple(args))

    def tap(self, name):
        self.s.button(name, True)
        self.s.button(name, False)

    def session(self):
        session = Mk3Session.__new__(Mk3Session)
        session.app, session.surface = self.a, self.s
        session.encoder_remainders, session.encoder_context = {}, None
        return session

    def answered(self, payload=PAYLOAD):
        bridge.receive(payload, time.monotonic())

    # --- keys and encoders on the right side --------------------------------------------
    def test_display_buttons_are_norns_keys(self):
        for name, key in (('DISPLAY 5', 1), ('DISPLAY 6', 2), ('DISPLAY 7', 3)):
            self.tap(name)
            self.assertEqual(self.sent(), [('/remote/key', key, 1), ('/remote/key', key, 0)])
            self.assertEqual(self.a.page, 'PLUGIN')

    def test_labels_and_screen_flag(self):
        keys = self.s.softkeys()
        self.assertEqual(tuple(keys[4:7]), ('K1', 'K2', 'K3'))
        self.assertNotEqual(keys[7], 'LOAD')
        self.assertTrue(snapshot(self.s).get('norns_screen'))

    def test_machinista_keeps_the_other_buttons(self):
        self.tap('DISPLAY 8')  # only the load page uses it
        self.tap('DISPLAY 3')
        self.assertEqual(self.sent(), [])

    def test_shift_keeps_machinista_functions(self):
        self.a.shift = True
        self.assertNotIn('K1', self.s.softkeys())
        self.tap('DISPLAY 7')
        self.tap('PAGE RIGHT')
        self.assertEqual(self.sent(), [])
        self.assertEqual(bridge.page, 1)
        self.answered()
        for index in (1, 4, 5):
            self.assertFalse(bridge.encoder(self.a, index, 80))  # left to Machinista
        self.assertEqual(self.sent(), [])
        self.a.shift = False

    def test_other_pages_and_plugins_are_untouched(self):
        self.a.page = 'CHANNEL'
        self.tap('DISPLAY 5')
        self.tap('PAGE RIGHT')
        self.assertEqual(self.sent(), [])
        self.a.page = 'PLUGIN'
        other = next(p['uri'] for p in self.a.plugin_catalog if p['kind'] == 'INSTRUMENT' and p['uri'] != URI and p['supported'])
        self.load(other)
        self.assertNotIn('K1', self.s.softkeys())
        self.tap('DISPLAY 7')
        self.assertEqual(self.sent(), [])
        self.assertNotIn('norns_panel', snapshot(self.s))

    def test_key_is_released_after_leaving_the_page(self):
        self.s.button('DISPLAY 5', True)
        self.a.page = 'CHANNEL'
        self.s.button('DISPLAY 5', False)
        self.assertEqual(self.sent(), [('/remote/key', 1, 1), ('/remote/key', 1, 0)])

    def test_encoders(self):
        session = self.session()
        session.rotate_raw(5, 4)
        self.assertEqual(self.sent(), [])
        session.rotate_raw(5, 4)
        self.assertEqual(self.sent(), [('/remote/enc', 1, 1)])
        session.rotate_raw(6, 3)
        session.rotate_raw(6, -8)  # reversing discards travel in the old direction
        self.assertEqual(self.sent(), [('/remote/enc', 2, -1)])
        session.rotate_raw(7, 24)
        self.assertEqual(self.sent(), [('/remote/enc', 3, 3)])
        session.rotate_raw(8, 80)
        self.assertEqual(self.sent(), [])

    def test_travel_can_be_tuned_while_running(self):
        path = Path(self.tmp.name) / 'travel'
        path.write_text('16\n')
        bridge.travel_file = path
        session = self.session()
        session.rotate_raw(5, 8)
        self.assertEqual(self.sent(), [])
        session.rotate_raw(5, 8)
        self.assertEqual(self.sent(), [('/remote/enc', 1, 1)])

    # --- PARAMS on the left side ----------------------------------------------------------
    def test_left_encoders_change_norns_params(self):
        before = self.a.track()['plugins'][0]['controls']['gain']
        session = self.session()
        session.rotate_raw(1, 8)  # no page from norns yet: consumed, nothing sent
        self.assertEqual(self.sent(), [])
        self.answered()
        session.rotate_raw(2, 16)
        session.rotate_raw(4, -8)
        self.assertEqual(self.sent(), [('/machinista/delta', 'note_length', 2, 1),
                                       ('/machinista/delta', 'root_note', -1, 1)])
        self.assertEqual(self.a.track()['plugins'][0]['controls']['gain'], before)

    def test_page_is_shown_on_the_left(self):
        self.answered()
        state = snapshot(self.s)
        panel = state['norns_panel']
        self.assertEqual((panel['title'], panel['page'], panel['pages']), ('STEP', 1, 3))
        self.assertEqual(panel['rows'][0], ('step division', '1/4', 0.5))
        self.assertEqual(state['fields'][:4], (('step division', '1/4'), ('note length', '50%'),
                                               ('scale mode', 'dorian'), ('root note', 'C3')))

    def test_silence_from_norns_is_reported(self):
        panel = snapshot(self.s)['norns_panel']
        self.assertEqual(panel['status'], 'NORNS IS NOT ANSWERING')
        self.assertEqual(snapshot(self.s)['fields'][:4], (None,) * 4)

    def test_page_buttons(self):
        self.answered()
        self.tap('PAGE RIGHT')
        self.tap('PAGE RIGHT')
        self.tap('PAGE RIGHT')  # norns said there are 3
        self.assertEqual(bridge.page, 3)
        self.tap('PAGE LEFT')
        self.assertEqual(bridge.page, 2)
        self.assertEqual(self.a.page, 'PLUGIN')

    def test_round_trip_through_osc(self):
        bridge.requested = 0
        bridge.poll()
        request = self.sent()
        self.assertEqual(request, [('/machinista/page', 1)])
        self.norns.sendto(osc('/machinista/page', PAYLOAD), self.reply_to)
        time.sleep(0.05)
        bridge.poll()
        self.assertEqual(bridge.info['section'], 'STEP')
        self.assertEqual(len(bridge.info['items']), 4)

    # --- loading a script with a PSET -------------------------------------------------------
    def test_load_page(self):
        for folder, files in (('awake', ('awake.lua',)), ('study', ('study1.lua', 'study2.lua')), ('mod', ())):
            (self.dust / 'code' / folder / 'lib').mkdir(parents=True)
            for name in files:
                (self.dust / 'code' / folder / name).write_text('')
        data = self.dust / 'data' / 'awake'
        data.mkdir(parents=True)
        (data / 'awake-01.pset').write_text('-- slow\n"x": 1\n')
        (data / 'awake-03.pset').write_text('"x": 1\n')
        self.answered()
        self.tap('PAGE LEFT')
        self.assertEqual(bridge.page, 0)
        self.assertEqual(self.s.softkeys()[7], 'LOAD')
        panel = snapshot(self.s)['norns_panel']
        self.assertEqual(panel['rows'][0][:2], ('SCRIPT', 'awake'))  # starts on the running script
        session = self.session()
        session.rotate_raw(2, 8)
        self.assertEqual(snapshot(self.s)['norns_panel']['rows'][1][:2], ('PSET', '01 slow'))
        self.tap('DISPLAY 8')
        self.assertEqual(self.sent(), [('/machinista/load', 'awake/awake.lua', 1)])
        self.assertEqual(bridge.page, 1)
        bridge.page = 0
        bridge.open_load_page()
        session.rotate_raw(1, 16)
        self.assertEqual(snapshot(self.s)['norns_panel']['rows'][0][1], 'study/study2')
        self.assertEqual(snapshot(self.s)['norns_panel']['rows'][1][1], 'NONE')

    def test_names(self):
        self.assertEqual(data_name('awake/awake.lua'), 'awake')
        self.assertEqual(data_name('study/study2.lua'), 'study/study2')
        self.assertEqual(psets('nothing/nothing.lua'), [(0, 'NONE')])
        self.assertEqual(scripts(self.dust / 'missing'), [])

    def test_panel_draws(self):
        from PIL import Image
        from app.norns_screen import overlay
        self.answered()
        state = snapshot(self.s)
        images = (Image.new('RGB', (480, 272)), Image.new('RGB', (480, 272)))
        left, _ = overlay(images, state)
        self.assertNotEqual(left.getbbox(), None)
        bridge.page = 0
        bridge.open_load_page()
        left, _ = overlay(images, snapshot(self.s))
        self.assertNotEqual(left.getbbox(), None)


if __name__ == '__main__':
    unittest.main()
