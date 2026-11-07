#!/usr/bin/env bash
# A Lua prompt for the running norns: shows matron's output and sends what you type.
T=$HOME/norns-stack/run
[ -p "$T/in" ] || { echo "norns is not running"; exit 1; }
tail -n 5 -f "$T/norns.log" & WATCH=$!
trap 'kill $WATCH' EXIT
cat > "$T/in"
