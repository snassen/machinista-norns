#!/usr/bin/env bash
# Restarts only the norns stack. Started by the norns menu, so it must outlive matron.
S=$HOME/norns-stack
sleep 1
bash $S/norns-stop.sh
sleep 1
bash $S/norns-start.sh
