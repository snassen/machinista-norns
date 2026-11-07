#!/usr/bin/env python3
"""Applies the screen bridge to matron and Machinista. Safe to run again.
Usage: apply_patches.py norns|machinista"""
import shutil
import sys
from pathlib import Path

HOME = Path.home()
URI = 'https://github.com/snassen/machinista-norns#bridge'

NORNS_CODE = '''// Machinista screen bridge: publishes each finished frame for the MK3 display.
#include <atomic>
#include <cstring>
#include <fcntl.h>
#include <sys/mman.h>
#include <unistd.h>

struct screen_shm_t {
    std::atomic<uint32_t> seq; // odd while a frame is being written
    uint32_t width, height, stride;
    uint8_t pixels[128 * 64 * 4];
};

static void screen_publish(void) {
    static screen_shm_t *shm = NULL;
    static bool failed = false;
    if (shm == NULL && !failed) {
        const int fd = shm_open("/norns-screen", O_CREAT | O_RDWR, 0660);
        void *p = MAP_FAILED;
        if (fd >= 0 && ftruncate(fd, sizeof(screen_shm_t)) == 0) {
            p = mmap(NULL, sizeof(screen_shm_t), PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
        }
        if (fd >= 0) {
            close(fd);
        }
        if (p == MAP_FAILED) {
            failed = true;
            return;
        }
        shm = static_cast<screen_shm_t *>(p);
    }
    if (shm == NULL) {
        return;
    }
    const int stride = cairo_image_surface_get_stride(surface);
    if (stride != 128 * 4) {
        return;
    }
    const uint32_t seq = shm->seq.load(std::memory_order_relaxed);
    shm->seq.store((seq | 1), std::memory_order_release);
    shm->width = 128;
    shm->height = 64;
    shm->stride = stride;
    memcpy(shm->pixels, cairo_image_surface_get_data(surface), sizeof(shm->pixels));
    shm->seq.store((seq | 1) + 1, std::memory_order_release);
}

void screen_update(void) {
'''

def replace_once(path, old, new, marker):
    text = path.read_text()
    if marker in text:
        print('already patched:', path)
        return
    if text.count(old) != 1:
        raise SystemExit(f'expected one match in {path}: {old[:50]!r}')
    backup = path.with_name(path.name + '.pre-norns')
    if not backup.exists():
        shutil.copy2(path, backup)
    path.write_text(text.replace(old, new))
    print('patched:', path)

def norns():
    path = HOME / 'norns-stack/norns/matron/src/hardware/screen.cc'
    replace_once(path, 'void screen_update(void) {\n', NORNS_CODE, 'screen_publish')
    replace_once(path, '    cairo_surface_flush(surface);\n    ssd1322_update(surface, surface_may_have_color);\n',
                 '    cairo_surface_flush(surface);\n    screen_publish();\n    ssd1322_update(surface, surface_may_have_color);\n',
                 'screen_publish();')

    # The script chooser with folders. It also doesn't assume a real norns' home directory.
    chooser = HOME / 'norns-stack/norns/lua/core/menu/select.lua'
    original = chooser.with_name('select.lua.pre-norns')
    if not original.exists():
        shutil.copy2(chooser, original)
    shutil.copy2(HOME / 'norns-stack/library/select.lua', chooser)
    print('installed:', chooser)

    # Menus: this norns is hosted by Machinista, so anything managing the whole device goes.
    core = HOME / 'norns-stack/norns/lua/core'
    replace_once(core / 'menu/home.lua', 'list = {"SELECT", "SYSTEM", "SLEEP"}', 'list = {"SELECT", "SYSTEM"}', 'list = {"SELECT", "SYSTEM"}')
    replace_once(core / 'menu/home.lua', '  for i=1,3 do\n    screen.move(0,25+10*i)', '  for i=1,#m.list do\n    screen.move(0,25+10*i)', 'for i=1,#m.list do')
    replace_once(core / 'menu/home.lua', "try 'SYSTEM > UPDATE'", "norns build is too old", "norns build is too old")
    replace_once(core / 'menu/system.lua',
                 'list = {"DEVICES > ", "WIFI >", "MODS >", "SETTINGS >", "RESTART", "UPDATE"},\n  pages = {"DEVICES", "WIFI", "MODS", "SETTINGS", "RESTART", "UPDATE"}',
                 'list = {"DEVICES > ", "MODS >", "SETTINGS >", "RESTART"},\n  pages = {"DEVICES", "MODS", "SETTINGS", "RESTART"}',
                 'list = {"DEVICES > ", "MODS >"')
    replace_once(core / 'menu/settings.lua',
                 'list = {"RESET", "PASSWORD >", "DISPLAY >", "BATTERY WARNING"},\n  pages = {"RESET", "PASSWORD", "DISPLAY"}',
                 'list = {"RESET"},\n  pages = {"RESET"}', 'list = {"RESET"}')
    # RESTART and RESET restart only norns, and RESTART comes back to the same script.
    replace_once(core / 'norns.lua', '_norns.reset = function()\n  _norns.system_action("reset", report_launch_failure("reset"))\nend',
                 '_norns.reset = function()\n  _norns.execute("setsid nohup bash ~/norns-stack/norns-restart.sh > ~/norns-stack/run/restart.log 2>&1 < /dev/null &")\nend',
                 'norns-restart.sh')
    replace_once(core / 'norns.lua', '  print("RESTARTING")\n  norns.script.clear()\n', '  print("RESTARTING")\n', '  print("RESTARTING")\n  norns.state.clean_shutdown')

def machinista():
    root = HOME / 'Machinista/app'
    shutil.copy2(Path(__file__).with_name('norns_screen.py'), root / 'norns_screen.py')
    shutil.copy2(Path(__file__).with_name('norns_input.py'), root / 'norns_input.py')
    shutil.copy2(Path(__file__).with_name('test_norns_bridge.py'), root.parent / 'tests/test_norns_bridge.py')
    replace_once(root / 'mk3_surface.py', "    def button(self,name,pressed):\n        import time\n        a=self.app\n",
                 "    def button(self,name,pressed):\n        import time\n        a=self.app\n"
                 "        from app.norns_input import bridge\n"
                 "        if bridge.button(a,name,pressed):return\n",
                 'norns_input')
    replace_once(root / 'mk3_surface.py', "        if self.app.page=='OPTIONS':keys=keys[:2]+('GRID',)+keys[3:]\n        return keys\n",
                 "        if self.app.page=='OPTIONS':keys=keys[:2]+('GRID',)+keys[3:]\n"
                 "        from app.norns_input import active\n"
                 "        if active(self.app):keys=tuple(keys[:4])+('K1','K2','K3')+tuple(keys[7:])\n"
                 "        return keys\n",
                 "active(self.app)")
    replace_once(root / 'mk3_session.py', "    def display_settings(self):\n",
                 "    def rotate_raw(self,index,delta):\n"
                 "        from app.norns_input import bridge\n"
                 "        if not bridge.encoder(self.app,index,delta):super().rotate_raw(index,delta)\n"
                 "    def display_settings(self):\n",
                 'norns_input')
    replace_once(root / 'mk2_display.py', "        if p=='PLUGIN' and selected:\n",
                 f"        if p=='PLUGIN' and selected and selected.get('uri')=='{URI}':state['norns_screen']=True\n        if p=='PLUGIN' and selected:\n",
                 'norns_screen')
    replace_once(root / 'mk3_display.py', "        return self.chain_animation(images,state)\n",
                 "        images=self.chain_animation(images,state)\n"
                 "        if state.get('norns_screen'):\n"
                 "            from app.norns_screen import overlay\n"
                 "            images=overlay(images);self.needs_refresh=True\n"
                 "        return images\n",
                 'norns_screen')

{'norns': norns, 'machinista': machinista}[sys.argv[1]]()
