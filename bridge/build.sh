#!/usr/bin/env bash
# Run on the Pi from this directory.
set -e
F="-O2 -std=c++17 -Wall -Wextra"
g++ $F -fPIC -shared -fvisibility=hidden norns_bridge_lv2.cpp -o norns-bridge.lv2/norns_bridge.so -static-libstdc++ -static-libgcc
g++ $F norns_bridged.cpp -o norns_bridged -ljack -lrt
g++ $F bridge_stat.cpp -o bridge_stat -lrt
g++ $F bridge_test.cpp -o bridge_test -ljack -ldl -lrt -lpthread
g++ $F -DNB_SHM_NAME='"/norns-bridge-test"' -fPIC -shared norns_bridge_lv2.cpp -o guard_test_plugin.so -static-libstdc++ -static-libgcc
g++ $F -DNB_SHM_NAME='"/norns-bridge-test"' guard_test.cpp -o guard_test -ldl -lrt
echo BUILD_OK; readelf -d norns-bridge.lv2/norns_bridge.so | grep NEEDED
