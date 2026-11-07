#!/usr/bin/env bash
# Starts JACK, SuperCollider, norns and the Machinista bridge in the background on the Pi.
S=$HOME/norns-stack; P=$S/prefix; T=$S/run; mkdir -p $T
export LD_LIBRARY_PATH=$P/lib PATH=$P/bin:$PATH JACK_NO_AUDIO_RESERVATION=1 QT_QPA_PLATFORM=offscreen
pgrep -x jackd >/dev/null && { echo ALREADY_RUNNING; exit 0; }
: > $T/norns.log; : > $T/bridge.log  # the waits below must never match the previous run
# Which script to run: the one named here, else the last one used, else Awake.
# After a clean restart norns resumes the last script by itself.
STATE=$HOME/dust/data/system.state
LAST=$(sed -n "s/^norns.state.script = '\(.*\)'$/\1/p" "$STATE" 2>/dev/null)
RESUMES=0; [ -z "$1" ] && grep -q 'clean_shutdown = true' "$STATE" 2>/dev/null && [ -n "$LAST" ] && RESUMES=1
SCRIPT=${1:-$LAST}; [ -n "$1" ] || [ -f "$SCRIPT" ] || SCRIPT=code/awake/awake.lua
setsid nohup jackd -R -P 15 -d dummy -r 48000 -p 256 > $T/jack.log 2>&1 < /dev/null &
for i in $(seq 1 50); do $T/jk >/dev/null 2>&1 && break; sleep 0.2; done
setsid nohup bash -c 'tail -f /dev/null | exec sclang -l ~/.config/SuperCollider/sclang_conf.yaml' > $T/sclang.log 2>&1 < /dev/null &
for i in $(seq 1 300); do ss -lun | grep -q ':57120 ' && break; sleep 0.2; done
rm -f $T/in; mkfifo $T/in
cd ~/norns; setsid nohup bash -c "tail -f /dev/null > $T/in" >/dev/null 2>&1 < /dev/null &
setsid nohup stdbuf -oL -eL ./build/norns/norns < $T/in > $T/norns.log 2>&1 &
# Timed by the clock, so the wait always lasts its full length.
READY=0; END=$((SECONDS+60))
while [ $SECONDS -lt $END ]; do grep -Fq 'available engines' $T/norns.log && { READY=1; break; }; sleep 0.2; done
# The bridge starts either way: it waits for crone's ports itself.
setsid nohup $S/bridge/norns_bridged > $T/bridge.log 2>&1 < /dev/null &
for i in $(seq 1 100); do grep -q BRIDGE_WIRED $T/bridge.log && break; sleep 0.2; done; cat $T/bridge.log
[ $READY = 1 ] && echo NORNS_READY || { echo NORNS_FAILED; tail -20 $T/norns.log; exit 1; }
[ $RESUMES = 1 ] || { sleep 1; printf 'norns.script.load("%s")\n' "$SCRIPT" > $T/in; }
