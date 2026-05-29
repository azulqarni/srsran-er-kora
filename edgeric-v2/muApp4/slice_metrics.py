#!/usr/bin/env python3
import os
import sys
import time

import zmq

PROTO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "protobufs"))
sys.path.append(PROTO_DIR)

from slice_metrics_pb2 import SliceMetrics
# from slice_budgets_pb2 import SliceBudgets  # once control proto exists

def main():
    ctx = zmq.Context.instance()

    # Subscribe to slice metrics
    sub = ctx.socket(zmq.SUB)
    sub.connect("ipc:///tmp/slice_metrics")
    sub.setsockopt(zmq.SUBSCRIBE, b"")
    sub.setsockopt(zmq.CONFLATE, 1)

    # Placeholder control PUB (no consumer in gNB yet)
    # pub = ctx.socket(zmq.PUB)
    # pub.connect("ipc:///tmp/control_slice_budgets")

    tti_counter = 0
    last = None
    while True:
        try:
            msg = sub.recv(zmq.NOBLOCK)
        except zmq.Again:
            time.sleep(0.001)
            continue

        sm = SliceMetrics()
        sm.ParseFromString(msg)
        # Handle wrap-around: accept decreasing tti as a wrap and keep going.
        if last is not None and sm.tti < last:
            last = sm.tti
        else:
            last = sm.tti

        do_print = False
        for s in sm.slices:
            if s.dl_rbs_used != 0 or s.ul_rbs_used != 0:
                do_print = True

        if do_print == False:
            # pass
            continue

        tti_counter += 1
        if tti_counter == 1000:
            tti_counter = 0
        else:
            # pass
            continue

        print(f"TTI {sm.tti}")
        for s in sm.slices:
            print(f"  slice={s.slice_id} min={s.cfg_min_prb} max={s.cfg_max_prb} "
                  f"prio={s.cfg_priority} dl_rbs={s.dl_rbs_used} ul_rbs={s.ul_rbs_used} "
                  f"avg_dl={s.avg_dl_rbs_per_slot:.1f} avg_ul={s.avg_ul_rbs_per_slot:.1f} "
                  f"hol_slots={s.hol_slots}")

        # When control exists, send a trivial policy: give all PRBs to the busiest slice.
        # busiest = max(sm.slices, key=lambda s: s.dl_backlog_bytes)  # once backlog is added
        # out = SliceBudgets()
        # out.tti = sm.tti + 1
        # b = out.budgets.add()
        # b.slice_id = busiest.slice_id
        # b.min_prb = busiest.cfg_max_prb
        # b.max_prb = busiest.cfg_max_prb
        # pub.send(out.SerializeToString())

if __name__ == "__main__":
    main()
