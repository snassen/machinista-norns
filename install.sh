#!/usr/bin/env bash
# Installs norns next to Machinista on a Raspberry Pi 4 or 5 and connects it to the MK3.
# Run it as the machinista user from a clone of this repository. Safe to run again:
# finished steps are skipped, so it also repairs and updates an existing install.
#
#   ./install.sh               norns, the bridge, the MK3 screen and controls, Awake
#   ./install.sh --pack        also the 44 tested scripts from their authors' repositories
#   ./install.sh --mi-ugens    also build Mutable Instruments UGens (needed by Terrarium)
#   ./install.sh --all         all of the above
#   ./install.sh --repatch     only re-apply the Machinista changes (after a Machinista update)
#
# Options: --yes (no questions), --jobs N (parallel build jobs, default 2).
set -euo pipefail

REPO=$(cd "$(dirname "$0")" && pwd)
S=$HOME/norns-stack
P=$S/prefix
JOBS=2; PACK=0; MI=0; YES=0; REPATCH=0
while [ $# -gt 0 ]; do
  case $1 in
    --pack) PACK=1 ;;
    --mi-ugens) MI=1 ;;
    --all) PACK=1; MI=1 ;;
    --repatch) REPATCH=1 ;;
    --yes) YES=1 ;;
    --jobs) JOBS=$2; shift ;;
    -h|--help) sed -n '2,13p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1"; exit 2 ;;
  esac
  shift
done

# Pinned versions: these are the ones tested together.
NORNS_REV=42df84420774ef4d0961cf37946ed30e4078920f   # v3.0.2-3
LIBMONOME_TAG=v1.4.9
NNG_TAG=v1.11
LIBLO_TAG=0.31          # 0.32 rejects OSC paths without a leading "/", which some engines send
LIBGPIOD_VERSION=1.6.4  # norns needs the 1.x API; Debian 13 ships 2.x
AWAKE_REV=73d4accfc090aaab58f1586eaf4d9cf54d3cff01
MI_UGENS_REV=8b67db8fd424af7999d783c292e339f59967ee4d
BRIDGE_URI='https://github.com/snassen/machinista-norns#bridge'
APT_PACKAGES="git cmake build-essential pkg-config autoconf automake libtool curl unzip
  jackd2 libjack-jackd2-dev libgpiod-dev libevdev-dev liblo-dev liblua5.3-dev lua5.3
  libncurses-dev libreadline-dev libsndfile1-dev libsdl2-dev libudev-dev libcairo2-dev
  libavahi-compat-libdnssd-dev libavahi-client-dev libboost-dev libnanomsg-dev libasound2-dev
  libglib2.0-dev supercollider-language supercollider-server sc3-plugins-language sc3-plugins-server"

step() { printf '\n\033[1m== %s\033[0m\n' "$*"; }
note() { printf '   %s\n' "$*"; }
fail() { printf '\033[31mERROR:\033[0m %s\n' "$*" >&2; exit 1; }
ask() {  # ask "question" -> 0 for yes. Defaults to yes.
  [ $YES = 1 ] && return 0
  read -r -p "   $1 [Y/n] " a < /dev/tty; [[ -z $a || $a =~ ^[Yy] ]]
}
build_env() {
  local glib; glib=$(pkg-config --cflags glib-2.0)
  export PKG_CONFIG_PATH=$P/lib/pkgconfig LD_LIBRARY_PATH=$P/lib
  export CFLAGS="-I$P/include $glib" CXXFLAGS="-I$P/include $glib" LDFLAGS="-L$P/lib"
}
clone() {  # clone url dir ref: a checkout of exactly that tag or commit
  local url=$1 dir=$2 ref=$3
  if [ ! -d "$dir/.git" ]; then git clone -q "$url" "$dir"; fi
  if [ "$(git -C "$dir" rev-parse HEAD)" != "$(git -C "$dir" rev-parse -q --verify "$ref^{commit}" 2>/dev/null)" ]; then
    git -C "$dir" fetch -q --tags origin 2>/dev/null || true
    git -C "$dir" -c advice.detachedHead=false checkout -q "$ref"
  fi
}
api() { curl -fsS -m 30 "http://localhost:8080$1" "${@:2}"; }

# ---------------------------------------------------------------------------------------
step "Checking this machine"
[ "$(id -u)" != 0 ] || fail "run this as the machinista user, not as root (it asks for sudo when needed)"
[ "$(uname -m)" = aarch64 ] || fail "needs a 64-bit Raspberry Pi OS (aarch64)"
[ -f "$HOME/Machinista/app/mk3_surface.py" ] || fail "Machinista was not found in ~/Machinista"
MODEL=$(tr -d '\0' < /proc/device-tree/model 2>/dev/null || echo unknown)
note "$MODEL, $(. /etc/os-release; echo "$PRETTY_NAME"), kernel $(uname -r)"
note "Machinista $(sed -n "s/^VERSION *= *['\"]\(.*\)['\"].*/\1/p" "$HOME/Machinista/app/branding.py")"
PAGE=$(getconf PAGESIZE)
if [ "$PAGE" != 4096 ]; then
  note "This kernel uses ${PAGE}-byte memory pages (the Pi 5 default). Untested with norns."
  note "If something crashes, add 'kernel=kernel8.img' to /boot/firmware/config.txt and reboot."
  ask "Continue anyway?" || exit 1
fi
case $MODEL in *"Pi 5"*) note "Pi 5: use the 27 W power supply, or USB ports are limited to 600 mA (grid + MK3 brown out)." ;; esac

install_files() {
  [ "$REPO" != "$S" ] || fail "clone this repository somewhere else than ~/norns-stack (for example ~/machinista-norns)"
  mkdir -p "$S"/{bridge,screen,library,run} "$P/bin"
  cp "$REPO"/bin/*.sh "$S/"
  cp -r "$REPO"/bridge/. "$S/bridge/"
  cp "$REPO"/screen/* "$S/screen/"
  cp "$REPO"/library/* "$S/library/"
  cp "$REPO"/tools/*.c "$S/"
  cp "$REPO/config/matronrc.lua" "$HOME/matronrc.lua"
}

patch_machinista() {
  step "Connecting the MK3 screen and controls to norns"
  python3 "$S/screen/apply_patches.py" machinista
  if ask "Restart Machinista now to load the change? (audio stops for a few seconds)"; then
    sudo systemctl restart machinista
  else
    note "Restart it later with: sudo systemctl restart machinista"
  fi
}

if [ $REPATCH = 1 ]; then install_files; patch_machinista; exit 0; fi

# ---------------------------------------------------------------------------------------
step "Installing system packages (asks for your password)"
# shellcheck disable=SC2086
sudo apt-get install -y --no-install-recommends --no-upgrade $APT_PACKAGES

step "Copying this project to $S"
install_files

step "Fetching sources"
cd "$S"
clone https://github.com/monome/norns.git norns $NORNS_REV
git -C norns submodule update -q --init --recursive
clone https://github.com/monome/libmonome.git libmonome $LIBMONOME_TAG
clone https://github.com/nanomsg/nng.git nng $NNG_TAG
clone https://github.com/radarsat1/liblo.git liblo-src $LIBLO_TAG
if [ ! -d libgpiod-$LIBGPIOD_VERSION ]; then
  curl -fsSLO https://mirrors.edge.kernel.org/pub/software/libs/libgpiod/libgpiod-$LIBGPIOD_VERSION.tar.xz
  tar xf libgpiod-$LIBGPIOD_VERSION.tar.xz
fi

step "Building libraries into $P (low priority, Machinista keeps playing)"
build_env
if [ ! -f "$P/lib/libmonome.so" ]; then
  (cd libmonome && python3 ./waf configure --prefix="$P" && nice -n 15 python3 ./waf -j"$JOBS" && python3 ./waf install)
fi
if [ ! -f "$P/lib/libnng.a" ]; then
  cmake -S nng -B nng/build -DCMAKE_INSTALL_PREFIX="$P" -DCMAKE_INSTALL_LIBDIR=lib -DNNG_TESTS=OFF -DNNG_TOOLS=OFF > /dev/null
  nice -n 15 cmake --build nng/build -j"$JOBS" && cmake --install nng/build > /dev/null
fi
if [ ! -f "$P/lib/libgpiod.a" ]; then
  (cd libgpiod-$LIBGPIOD_VERSION && ./configure --prefix="$P" --enable-tools=no --enable-static --disable-shared > /dev/null \
    && nice -n 15 make -j"$JOBS" > /dev/null && make install > /dev/null)
fi
if [ ! -f "$P/lib/liblo.so.7.4.1" ]; then
  cmake -S liblo-src/cmake -B liblo-src/build-pi -DCMAKE_INSTALL_PREFIX="$P" -DCMAKE_INSTALL_LIBDIR=lib -DWITH_TESTS=OFF -DWITH_CPP_TESTS=OFF -DWITH_EXAMPLES=OFF -DWITH_TOOLS=OFF > /dev/null
  nice -n 15 cmake --build liblo-src/build-pi -j"$JOBS" && cmake --install liblo-src/build-pi > /dev/null
fi
note "libraries ready"

step "Building norns"
cd "$S/norns"
if git apply --check "$REPO/patches/norns-pi-debian13.patch" 2>/dev/null; then
  git apply "$REPO/patches/norns-pi-debian13.patch"; note "applied the Debian 13 build fixes"
elif git apply --check --reverse "$REPO/patches/norns-pi-debian13.patch" 2>/dev/null; then
  note "Debian 13 build fixes already applied"
else
  fail "the norns build patch does not fit this norns checkout"
fi
python3 "$S/screen/apply_patches.py" norns
python3 ../libmonome/waf configure --prefix="$P" > "$S/run/norns-configure.log" 2>&1 || { tail -20 "$S/run/norns-configure.log"; fail "norns configure failed"; }
nice -n 15 python3 ../libmonome/waf build -j"$JOBS" > "$S/run/norns-build.log" 2>&1 || { tail -30 "$S/run/norns-build.log"; fail "norns build failed (log: $S/run/norns-build.log)"; }
[ -x build/norns/norns ] || fail "norns binary is missing after the build"
note "norns built"

step "Building the bridge and helper tools"
cd "$S"
cc -O2 jk.c -o run/jk -ljack
cc -O2 peak.c -o run/peak -ljack -lm
cc -O2 jack_connect.c -o "$P/bin/jack_connect" -ljack
(cd bridge && bash build.sh > "$S/run/bridge-build.log" 2>&1) || { tail -20 "$S/run/bridge-build.log"; fail "bridge build failed"; }
note "bridge built"

step "Setting up SuperCollider and the norns folders"
mkdir -p ~/dust/code ~/dust/data ~/dust/audio ~/.config/SuperCollider ~/.local/share/SuperCollider/Extensions
[ -e ~/norns ] || ln -s "$S/norns" ~/norns
cat > ~/.config/SuperCollider/sclang_conf.yaml <<YAML
includePaths:
  - $HOME/norns/sc/core
  - $HOME/norns/sc/engines
  - $HOME/dust
excludePaths: []
postInlineWarnings: false
excludeDefaultPaths: false
YAML
(cd ~/norns/sc && ./install.sh)
# Some scripts refuse to run when they can't read a norns version.
[ -f ~/version.txt ] || echo 260819 > ~/version.txt
clone https://github.com/tehn/awake.git ~/dust/code/awake $AWAKE_REV
# Many scripts write to /home/we, the home folder on a real norns.
if [ ! -e /home/we ]; then
  note "linking /home/we to $HOME (asks for your password)"
  sudo ln -s "$HOME" /home/we
fi

step "Sharing samples with Machinista"
mkdir -p ~/Machinista/samples/norns-tape
[ -e ~/dust/audio/machinista ] || ln -s ~/Machinista/samples ~/dust/audio/machinista
if [ ! -e ~/dust/audio/tape ]; then ln -s ~/Machinista/samples/norns-tape ~/dust/audio/tape
elif [ ! -L ~/dust/audio/tape ]; then note "~/dust/audio/tape is a real folder, left as it is"; fi
note "norns sees Machinista's samples in audio/machinista; norns tape recordings land in Machinista's norns-tape folder"

step "Installing the Norns Bridge plug-in in Machinista"
api /api/session > /dev/null || fail "Machinista's web service is not answering on port 8080"
INSTALLED=/var/lib/machinista/lv2/plugins/norns-bridge.lv2/norns_bridge.so
if [ -f "$INSTALLED" ] && cmp -s "$INSTALLED" "$S/bridge/norns-bridge.lv2/norns_bridge.so"; then
  note "already installed and up to date"
else
  BUNDLE=$(api /api/lv2 | python3 -c "
import json,sys
for b in json.load(sys.stdin)['bundles']:
    if any(p['uri']=='$BRIDGE_URI' for p in b['plugins']): print(b['id'], b.get('canDelete', True))")
  TOKEN=$(api /api/session | python3 -c 'import json,sys;print(json.load(sys.stdin)["token"])')
  if [ -n "$BUNDLE" ]; then
    set -- $BUNDLE
  fi
  if [ -n "$BUNDLE" ] && [ "$2" != True ]; then
    note "A different Norns Bridge build is loaded on a pad, so it was left in place."
    note "To update it: remove it from the pad, then run ./install.sh again."
  else
    if [ -n "$BUNDLE" ]; then
      api "/api/lv2/delete?id=$1" -X POST -H "X-Machinista-Token: $TOKEN" > /dev/null
      sleep 2
    fi
    rm -f "$S/run/norns-bridge.zip"
    (cd "$S/bridge" && python3 -m zipfile -c "$S/run/norns-bridge.zip" norns-bridge.lv2/manifest.ttl norns-bridge.lv2/norns-bridge.ttl norns-bridge.lv2/norns_bridge.so)
    api "/api/lv2/upload?name=norns-bridge.zip" -X POST -H "X-Machinista-Token: $TOKEN" \
      -H "Content-Type: application/octet-stream" --data-binary @"$S/run/norns-bridge.zip" > /dev/null
    for _ in $(seq 1 30); do
      api /api/lv2 | grep -q "$BRIDGE_URI" && break; sleep 1
    done
    api /api/lv2 | grep -q "$BRIDGE_URI" || fail "Machinista did not list the plug-in after the upload"
    note "installed"
  fi
fi

# ---------------------------------------------------------------------------------------
if [ $MI = 1 ]; then
  step "Building Mutable Instruments UGens (takes a while)"
  if [ -f ~/.local/share/SuperCollider/Extensions/mi-UGens/MiBeads.so ]; then
    note "already installed"
  else
    SC_VERSION=$(dpkg-query -W -f '${Version}' supercollider-server | sed 's/^[0-9]*://; s/[+~-].*//')
    clone https://github.com/carlosedp/norns-mi-UGens.git "$S/mi-ugens" $MI_UGENS_REV
    git -C "$S/mi-ugens" submodule update -q --init --recursive --depth 1
    # Its last step checks for 32-bit norns binaries and fails on 64-bit ones, so the result is checked here instead.
    (cd "$S/mi-ugens" && nice -n 15 ./build.sh --native --pr 24 --sc-version "Version-$SC_VERSION" --jobs "$JOBS") || true
    OUT=$S/mi-ugens/work/out/mi-UGens
    [ -f "$OUT/MiBeads.so" ] && readelf -h "$OUT/MiBeads.so" | grep -q AArch64 || fail "mi-UGens build failed"
    rm -rf ~/.local/share/SuperCollider/Extensions/mi-UGens
    cp -r "$OUT" ~/.local/share/SuperCollider/Extensions/mi-UGens
    note "installed"
  fi
fi

if [ $PACK = 1 ]; then
  step "Installing the tested script pack"
  grep -v '^#' "$S/library/pack.txt" | while read -r dir url rev; do
    [ -n "$dir" ] || continue
    if [ -d ~/dust/code/"$dir" ]; then continue; fi
    if clone "$url" ~/dust/code/"$dir" "$rev" 2>/dev/null; then note "$dir"; else note "$dir: download failed, skipped"; rm -rf ~/dust/code/"$dir"; fi
  done
  # Terrarium installs its UGens to /home/we by name.
  [ -f ~/dust/code/terrarium/lib/installer.lua ] && sed -i 's|^local EXT = "/home/we/|local EXT = _path.home .. "/|' ~/dust/code/terrarium/lib/installer.lua
  [ $MI = 1 ] || [ ! -d ~/dust/code/terrarium ] || note "terrarium needs --mi-ugens to make sound"
fi

step "Writing the script library folders"
if [ -f ~/dust/data/script-library.lua ]; then
  note "~/dust/data/script-library.lua already exists, kept (delete it to regenerate)"
else
  python3 "$S/library/make_library.py" "$S/library/catalog-survey.json" ~/dust/data/script-library.lua
  note "written"
fi

patch_machinista

step "Done"
note "Start norns:    bash ~/norns-stack/norns-start.sh"
note "Stop norns:     bash ~/norns-stack/norns-stop.sh"
note "Lua prompt:     bash ~/norns-stack/norns-repl.sh"
note "On the MK3: load the Norns Bridge instrument on a Sound (browser: F1 for INSTRUMENT)."
