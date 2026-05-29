#!/usr/bin/env python3
"""
Toy μApp that assigns all scheduling weight to the first UE it sees and zero to everyone else.
Useful for sanity-checking the RT-E2 control path.
"""

import os
import sys
import time

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from collections import OrderedDict
from edgeric_messenger import EdgericMessenger


def build_weights(ue_dict):
    """Return the alternating [rnti, weight, ...] list expected by the RAN."""
    if not ue_dict:
        return []

    # Preserve UE ordering so the "first" UE is deterministic.
    ordered = OrderedDict(sorted(ue_dict.items()))
    first_rnti = next(iter(ordered.keys()))

    weights = []
    for rnti in ordered:
        weight = 1.0 if rnti == first_rnti else 0.0
        weights.extend([float(rnti), weight])
    return weights


def main():
    messenger = EdgericMessenger(socket_type="weights")
    while True:
        try:
            tti, ue_data = messenger.get_metrics(flag_print=False)
            weight_vector = build_weights(ue_data)
            if weight_vector:
                messenger.send_scheduling_weight(tti, weight_vector, flag_print=False)
                print(f"[μApp] TTI={tti} weights sent: {weight_vector}")
            else:
                print("[μApp] No UEs yet, skipping...")
        except KeyboardInterrupt:
            break
        except Exception as err:
            print(f"[μApp] Error: {err}")
            time.sleep(0.5)

        time.sleep(0.02)  # light pacing to avoid hammering Redis


if __name__ == "__main__":
    main()
