# hedera.py
#
# Reproducing the results of the Hedera paper.
#
# Running the POX controller:
# $ ~/pox/pox.py controllers.riplpox --topo=ft,4 --routing=hashed --mode=reactive
#
# Running Mininet via this script (second terminal window)
# $ sudo python LaunchExperiment.py


import os
import json
import subprocess

from time import time, sleep
from math import sqrt

import re
from mininet.node import RemoteController
from mininet.net import Mininet
from mininet.log import lg
from mininet.util import dumpNodeConnections
from mininet.cli import CLI

from ripllib.dctopo import FatTreeTopo
from applauncher import HadoopTest

from multiprocessing import Process, Value

# Flag to indicate if the emulation is working or not
IS_ALIVE = Value('i', 0)

# We must skip at least the first sample to establish a baseline bytes_recvd
SAMPLES_TO_SKIP = 1

IPERF_PATH = '/usr/bin/iperf'
IPERF_PORT = 5001
IPERF_PORT_BASE = 5001
IPERF_SECONDS = 3600


HOST_NAMES = ('0_0_2', '0_0_3', '0_1_2', '0_1_3',
              '1_0_2', '1_0_3', '1_1_2', '1_1_3',
              '2_0_2', '2_0_3', '2_1_2', '2_1_3',
              '3_0_2', '3_0_3', '3_1_2', '3_1_3')

lg.setLogLevel('info')


def avg(lst):
    return float(sum(lst)) / len(lst)


def variance(lst):
    mean = avg(lst)
    diffs_sqrd = []
    for val in lst:
        diffs_sqrd.append((val - mean) ** 2)
    return avg(diffs_sqrd)


def bytes_to_throughputs(rxbytes, durations):
    """
    Convert samples of cumulative bytes received to bytes per second.
    If rxbytes has N_SAMPLES, then throughputs has N_SAMPLES - 1.
    """
    throughputs = {}
    for name in HOST_NAMES:
        throughputs[name] = []

    for name in rxbytes:
        for i, sample in enumerate(rxbytes[name]):
            if i:  # i > 0
                throughput = (sample - rxbytes[name][i - 1]) / durations[i]
                throughputs[name].append(throughput)

    return throughputs


def sample_rxbytes(net, rxbytes):
    """
    For each host, parse received bytes from /proc/net/dev, which has the
    format:

    Inter-|   Receive                                             |  Transmit
     face |bytes packets errs drop fifo frame compressed multicast|bytes packets errs drop fifo colls carrier compressed
    lo:       0       0    0    0    0     0          0         0        0       0    0    0    0     0       0          0
3_1_3-eth0: 33714765732 1133072    0    0    0     0          0         0 25308223734 1110457    0    0    0     0       0          0
    """
    for name in HOST_NAMES:
        host = net.get(name)
        iface = '%s-eth0:' % name
        bytes = None

        res = host.cmd('cat /proc/net/dev')
        lines = res.split('\n')
        for line in lines:
            if iface in line:
                bytes = int(line.split()[1])
                rxbytes[name].append(bytes)
                break

        if bytes is None:
            lg.error('Couldn\'t parse rxbytes for host %s!\n' % name)


def aggregate_statistics(rxbytes, sample_durations):
    """
    Return the average throughput and variance summed over each host, in
    bytes/sec
    """
    throughputs = bytes_to_throughputs(rxbytes, sample_durations)

    agg_throughput = 0.0
    agg_variance = 0.0

    for name in throughputs:
        agg_throughput += avg(throughputs[name])
        agg_variance += variance(throughputs[name])

    return (agg_throughput, agg_variance)


def kill_controller():
    ports = ['6633']
    popen = subprocess.Popen(['netstat', '-lpn'],
                             shell=False,
                             stdout=subprocess.PIPE)
    (data, err) = popen.communicate()
    pattern = "^tcp.*((?:{0})).* (?P<pid>[0-9]*)/.*$"
    pattern = pattern.format(')|(?:'.join(ports))
    prog = re.compile(pattern)
    for line in data.split('\n'):
        match = re.match(prog, line)
        if match:
            pid = match.group('pid')
            subprocess.Popen(['kill', '-9', pid])


def sample_bandwidth(net):
    # Sample the cumulative # of bytes received for each host, every second.
    # The diff between adjacent samples gives us throughput for that second.
    rxbytes = {}
    sample_durations = []
    for name in HOST_NAMES:
        rxbytes[name] = []

    now = time()
    i = 0
    while IS_ALIVE.value == 1:
        print 'Taking sample %d...' % (i,)
        sample_durations.append(time() - now)
        now = time()
        sample_rxbytes(net, rxbytes)
        sleep(1.0)
        i += 1

    print 'Captured Rxbytes from /proc/net/dev = %s' % rxbytes

    (agg_mean, agg_var) = aggregate_statistics(rxbytes, sample_durations)
    agg_stddev = sqrt(agg_var)
    mean_gbps = agg_mean / (2 ** 30) * 8
    stddev_gbps = agg_stddev / (2 ** 30) * 8
    print 'Total average throughput: %f bytes/sec (%f Gbps)' % \
          (agg_mean, mean_gbps)
    print 'Standard deviation: %f bytes/sec (%f Gbps)' % \
          (agg_stddev, stddev_gbps)


def main():
    print 'Running 16-host fat-tree Mininet topology and starting Hadoop emulation on each'

    # Shut down iperf processes
    os.system('killall -9 ' + IPERF_PATH)

    start = time()
    topo = FatTreeTopo(k=4, speed=1.0)  # 1.0 Gbps links
    net = Mininet(topo=topo)
    net.addController(name='SDNController', controller=RemoteController,
                      ip='127.0.0.1', port=6633)
    net.start()
    dumpNodeConnections(net.hosts)

    sleep(5)

    # CLI(net)

    hosts = net.hosts
    emulation = Process(target=HadoopTest, args=(hosts, ))
    emulation.start()   # Started Hadoop emulation on one process

    IS_ALIVE.value = 1

    sample = Process(target=sample_bandwidth, args=(net,))
    sample.start()      # Starting sampling of throughput using 2nd process

    emulation.join()
    IS_ALIVE.value = 0
    sample.join()

    # Kill the controller before to prevent exceptions

    kill_controller()

    net.stop()

    print 'All done in %0.2fs!' % (time() - start)


if __name__ == '__main__':
    try:
        main()
    except:
        print '-' * 80
        print 'Caught exception.  Cleaning up...'
        print '-' * 80
        import traceback

        traceback.print_exc()
        os.system('killall -9 top bwm-ng tcpdump cat mnexec iperf; mn -c')
