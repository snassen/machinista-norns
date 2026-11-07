#!/usr/bin/env bash
# Runs the drift test at several clock offsets next to a running Machinista. Needs no norns.
cd "$(dirname "$0")"; SECS=${1:-60}
jackd -R -P 15 -d dummy -r 48000 -p 256 > /tmp/bridge-jack.log 2>&1 & JP=$!
sleep 2; ./norns_bridged --no-crone & DP=$!; sleep 1
rc=0; for ppm in 0 200 -200; do ./bridge_test ./norns-bridge.lv2/norns_bridge.so $ppm $SECS; r=$?; echo "EXIT ppm=$ppm rc=$r"; [ $r = 0 ] || rc=1; done
kill $DP; sleep 1; kill $JP; wait 2>/dev/null; echo "TEST_RC=$rc"
