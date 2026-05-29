#!/usr/bin/env python3
import os
import sys
import time

import zmq

PROTO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "protobufs"))
sys.path.append(PROTO_DIR)

from slice_metrics_pb2 import SliceMetrics
from metrics_pb2 import Metrics

# Simple helper to compute modular distance for tti wrap if needed.

def main():
    ctx = zmq.Context.instance()

    sub_slice = ctx.socket(zmq.SUB)
    sub_slice.connect("ipc:///tmp/slice_metrics")
    sub_slice.setsockopt(zmq.SUBSCRIBE, b"")
    sub_slice.setsockopt(zmq.CONFLATE, 1)

    sub_ue = ctx.socket(zmq.SUB)
    sub_ue.connect("ipc:///tmp/metrics")
    sub_ue.setsockopt(zmq.SUBSCRIBE, b"")
    sub_ue.setsockopt(zmq.CONFLATE, 1)

    poller = zmq.Poller()
    poller.register(sub_slice, zmq.POLLIN)
    poller.register(sub_ue, zmq.POLLIN)

    last_slice_tti = None
    last_ue_tti = None

    counter = 0
    interval = 1000  # print every N TTIs where messages are received

    while True:
        socks = dict(poller.poll(1000))

        if sub_slice in socks:
            sm = SliceMetrics()
            sm.ParseFromString(sub_slice.recv())
            last_slice_tti = sm.tti
            counter += 1
            if counter % interval != 0:
                continue

            print(f"[SliceMetrics] tti={sm.tti}")
            for s in sm.slices:
                print(f"  slice={s.slice_id} min={s.cfg_min_prb} max={s.cfg_max_prb} prio={s.cfg_priority} "
                      f"dl_rbs={s.dl_rbs_used} ul_rbs={s.ul_rbs_used} avg_dl={s.avg_dl_rbs_per_slot:.1f} "
                      f"avg_ul={s.avg_ul_rbs_per_slot:.1f} hol_slots={s.hol_slots}")

        if sub_ue in socks:
            um = Metrics()
            um.ParseFromString(sub_ue.recv())
            last_ue_tti = um.tti_cnt
            counter += 1
            if counter % interval != 0:
                continue

            print(f"[UeMetrics] tti={um.tti_cnt}")
            for ue in um.ue_metrics:
                print(f"  rnti={ue.rnti} cqi={ue.cqi} snr={ue.snr:.1f} tx_bytes={ue.tx_bytes:.0f} "
                      f"rx_bytes={ue.rx_bytes:.0f} dl_buf={ue.dl_buffer} ul_buf={ue.ul_buffer} dl_tbs={ue.dl_tbs:.0f}")

        if sub_slice not in socks and sub_ue not in socks:
            print("waiting for metrics...")

if __name__ == "__main__":
    main()
