#!/bin/bash

# cleanup previous executions
./cleanup.sh
mkdir /tmp/iperf
killall -9 python2.7
sudo mn -c
sleep 5

~/pox/pox.py controllers.proactiveController --topo=ft,4 &
sleep 2
sudo python LaunchExperiment.py
sleep 6