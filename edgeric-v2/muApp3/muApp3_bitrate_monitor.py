#!/usr/bin/env python3
"""
Simple μApp that subscribes to RT‑E2 metrics and prints per‑UE TX/RX bitrates every 5 seconds.
It derives rates by differencing the cumulative byte counters reported by the RAN.
"""

import os
import sys
import time
from collections import defaultdict

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from edgeric_messenger import EdgericMessenger

REPORT_INTERVAL = 5.0


def main():
    messenger = EdgericMessenger(socket_type="None")
    last_bytes = defaultdict(lambda: {"tx": 0.0, "rx": 0.0})
    last_sample_time = time.time()

    try:
        while True:
            _, ue_data = messenger.get_metrics(flag_print=False)
            now = time.time()

            elapsed = now - last_sample_time
            if elapsed >= REPORT_INTERVAL and ue_data:
                print(f"\nBitrate report (Dt={elapsed:.1f}s)")
                slice_tx_bits = defaultdict(float)
                slice_rx_bits = defaultdict(float)
                for rnti, metrics in sorted(ue_data.items()):
                    total_tx = metrics.get("tx_bytes", 0.0)
                    total_rx = metrics.get("rx_bytes", 0.0)
                    slice_id = metrics.get("slice_id", 0)

                    delta_tx = total_tx - last_bytes[rnti]["tx"]
                    delta_rx = total_rx - last_bytes[rnti]["rx"]
                    if delta_tx < 0:
                        delta_tx = total_tx
                    if delta_rx < 0:
                        delta_rx = total_rx

                    tx_bps = (delta_tx * 8.0) / elapsed
                    rx_bps = (delta_rx * 8.0) / elapsed
                    print(f"UE {rnti} (slice {slice_id}): TX={tx_bps/1e6:.3f} Mbps  RX={rx_bps/1e6:.3f} Mbps")

                    slice_tx_bits[slice_id] += delta_tx * 8.0
                    slice_rx_bits[slice_id] += delta_rx * 8.0

                    last_bytes[rnti]["tx"] = total_tx
                    last_bytes[rnti]["rx"] = total_rx

                for sid in sorted(slice_tx_bits.keys()):
                    tx_mbps = slice_tx_bits[sid] / elapsed / 1e6
                    rx_mbps = slice_rx_bits[sid] / elapsed / 1e6
                    print(f"Slice {sid}: TX={tx_mbps:.3f} Mbps  RX={rx_mbps:.3f} Mbps")

                last_sample_time = now

            time.sleep(0.05)
    except KeyboardInterrupt:
        print("\nBitrate monitor stopped.")


if __name__ == "__main__":
    main()
