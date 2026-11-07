#!/usr/bin/env bash
pkill -x norns_bridged; pkill -x norns; pkill -x sclang; pkill -x scsynth; sleep 0.5; pkill -x jackd
pkill -f 'tail -f /dev/null' ; rm -f "$HOME/norns-stack/run/in"; echo STOPPED
