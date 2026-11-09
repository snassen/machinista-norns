# machinista-norns

> **Superseded.** This Pi 4 add-on is no longer developed. Its successor, with several norns instances, Norns Instrument / Norns Effect plug-ins, clock and MIDI sync and a NORNS mode on the MK3, is the [`norns-mode` branch of snassen/machinista](https://github.com/snassen/machinista/tree/norns-mode) (see `docs/norns/` there).

Run [norns](https://github.com/monome/norns) inside [Machinista](https://github.com/photogeen/machinista) on a Raspberry Pi 4 or 5, with a Maschine MK3 as its screen, keys and encoders.

> **Unofficial.** This is a community add-on. It is not made or endorsed by the Machinista project, monome or Native Instruments. It changes a few of Machinista's files, so a Machinista update can undo it. After an update, run `./install.sh --repatch` and let it restart Machinista: the changes only take effect after a restart.

## What you get

- **Audio:** a *Norns Bridge* instrument plug-in for Machinista. Load it on a Sound, and norns plays through that Sound's channel, effects and mixer. The plug-in's sidechain input feeds audio into norns, for example the MK3's inputs (sidechain source *EXT STEREO*) or another group or sound.
- **Screen:** while the Norns Bridge plug-in page is open, the right MK3 display shows the norns screen (128×64, scaled 3×).
- **Controls:** on that page, display buttons 5–7 are norns K1–K3 and encoders 5–7 are E1–E3. Hold SHIFT to reach Machinista's own functions.
- **Parameters:** encoders 1–4 change the running script's PARAMS, four at a time, with names, values and bars on the left display. The page buttons step through them, section by section, the same order as the norns PARAMS menu (the script's own first, then levels, reverb, clock...).
- **Script and preset loading:** one page left of the first parameter page is LOAD. Encoder 1 picks a script, encoder 2 one of its saved PSETs, and display button 8 loads both. PSETs stay in norns' own format and folders.
- **Script library:** the norns SELECT menu is grouped into folders (loopers, samplers, effects, sequencers, generative, instruments, utilities) with a scrolling description. E3 adds a favorite.
- **Menus** trimmed for a hosted norns: no Wi-Fi, update, password or sleep. RESTART restarts only norns.
- **Shared samples:** norns sees Machinista's samples in `audio/machinista`. norns tape recordings land in Machinista's `norns-tape` folder.
- **Grid:** a monome grid plugged into the Pi works as on a real norns.
- **Optional script pack:** 44 community scripts (mlr, cranes, otis, cheat codes 2, orca, mlre and more), each tested to load on a Pi 4. They are downloaded from their authors' repositories at a pinned commit and keep their own licenses.

## Requirements

- Raspberry Pi 4 or 5 running Machinista (tested with 0.1.0-beta.17, Debian 13).
- A Maschine MK3. Give it its own power supply if you also plug in a grid.
- About 2 GB free space. An internet connection during the install.
- Pi 5: use the official 27 W power supply. Without it the Pi limits USB current, and the MK3 and grid can brown out.

## Install

Log in to the Pi as the `machinista` user:

```bash
git clone https://github.com/snassen/machinista-norns.git ~/machinista-norns
cd ~/machinista-norns
./install.sh --all
```

It asks for your password for the system packages, and at the end asks to restart Machinista. Everything is built from source on the Pi, at low priority so Machinista keeps playing. On a Pi 4 this takes about 20–30 minutes, longer with `--mi-ugens`.

| Option | What it adds |
| --- | --- |
| *(none)* | norns, the bridge, the MK3 screen and controls, the script library, Awake |
| `--pack` | the 44 tested scripts |
| `--mi-ugens` | Mutable Instruments UGens for SuperCollider (Terrarium needs them) |
| `--all` | both |
| `--repatch` | only re-apply the Machinista changes, after a Machinista update |

The installer is safe to run again. It skips finished steps and repairs anything missing.

## Use

```bash
bash ~/norns-stack/norns-start.sh            # start norns (resumes the last script)
bash ~/norns-stack/norns-start.sh code/otis/otis.lua
bash ~/norns-stack/norns-stop.sh
bash ~/norns-stack/norns-repl.sh             # a Lua prompt into the running norns
```

On the MK3, go to a Sound's plug-in slot, press F1 to switch the browser to INSTRUMENT, and load **Norns Bridge**. Load it only once: a second copy stays silent and shows as refused.

## How it works

```
norns (matron, crone, SuperCollider)
   │  JACK, dummy driver, 48 kHz / 256 frames
norns_bridged ──── shared memory /norns-bridge-v2 ──── Norns Bridge LV2 plug-in in Machinista
matron screen ──── shared memory /norns-screen ─────── MK3 right display overlay
MK3 buttons/encoders ── OSC /remote/key, /remote/enc ──► matron
```

The plug-in and the daemon each keep a small buffer and resample to absorb clock drift between JACK's dummy clock and the MK3's audio clock.

Everything lives in `~/norns-stack` (sources, builds, a private prefix with libmonome, nng, libgpiod 1.6 and liblo 0.31) and `~/dust`, as on a real norns. `/home/we` is linked to the machinista home, because many scripts use that path.

## Layout

| Folder | Contents |
| --- | --- |
| `bridge/` | the LV2 plug-in, the JACK daemon, drift and guard tests |
| `screen/` | screen and input bridges for Machinista, plus the patcher for matron and Machinista |
| `library/` | the folder chooser (`select.lua`), library generator, script pack list, batch tester |
| `patches/` | build fixes for norns on Debian 13 / 64-bit |
| `bin/` | start, stop, restart and REPL scripts |
| `norns-mod/` | the `machinista` norns mod that answers the MK3's parameter and load requests |
| `tools/` | small JACK helpers |

## License

GPL-3.0-or-later, the same as norns and Machinista. Scripts in the optional pack are not part of this repository. Each is downloaded from its author and keeps its own license.
