#!/usr/bin/env python3
import os
import sys
import time

import zmq

PROTO_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "protobufs"))
sys.path.append(PROTO_DIR)

from slice_budgets_pb2 import SliceBudgets

SLICE_IDS = [2, 3]
PRB_TOTAL = 273
HEAVY_SHARE = 0.99
FLIP_PERIOD_S = 10.0
PUBLISH_TICK_S = 0.05

def build_budgets(slice_ids, prb_total, heavy_first):
    msg = SliceBudgets()
    # tti field is ignored by the gNB today; set nonzero for clarity
    msg.tti = int(time.time() * 1000) & 0xFFFFFFFF
    for idx, sid in enumerate(slice_ids):
        b = msg.budgets.add()
        b.slice_id = sid
        # heavy slice gets 90%, light slice 10%
        share = HEAVY_SHARE if ((idx == 0) == heavy_first) else (1.0 - HEAVY_SHARE)
        prbs = max(1, int(prb_total * share))
        b.min_prb = prbs
        b.max_prb = prbs  # hard cap at the same share; loosen if desired
        b.priority = 128  # leave midpoint; adjust if you want
    return msg

def main():
    ctx = zmq.Context.instance()
    pub = ctx.socket(zmq.PUB)
    pub.setsockopt(zmq.CONFLATE, 1)
    # The gNB subscribes with connect(), so the muApp must bind.
    pub.bind("ipc:///tmp/control_slice_budgets")

    heavy_first = True
    next_flip = time.time() + FLIP_PERIOD_S

    while True:
        now = time.time()
        if now >= next_flip:
            heavy_first = not heavy_first
            next_flip = now + FLIP_PERIOD_S

        msg = build_budgets(SLICE_IDS, PRB_TOTAL, heavy_first)
        pub.send(msg.SerializeToString())
        time.sleep(PUBLISH_TICK_S)

if __name__ == "__main__":
    main()
