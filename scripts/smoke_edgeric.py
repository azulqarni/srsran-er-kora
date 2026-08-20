#!/usr/bin/env python3
"""Import and serialization smoke checks for EdgeRIC's generated bindings."""

from __future__ import annotations

import argparse
import importlib
import io
from pathlib import Path
import subprocess
import sys


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--with-muapps",
        action="store_true",
        help="also import the optional legacy muApp dependency stack",
    )
    args = parser.parse_args()

    sys.dont_write_bytecode = True
    repo_root = Path(__file__).resolve().parents[1]
    edgeric_dir = repo_root / "edgeric-v2"
    protobuf_dir = edgeric_dir / "protobufs"
    sys.path[:0] = [str(edgeric_dir), str(protobuf_dir)]

    import google.protobuf
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy
    import zmq
    from protobufs import control_mcs_pb2
    from protobufs import control_weights_pb2
    from protobufs import metrics_pb2
    from protobufs import slice_budgets_pb2
    from protobufs import slice_metrics_pb2

    messages = (
        control_mcs_pb2.mcs_control,
        control_weights_pb2.SchedulingWeights,
        metrics_pb2.UeMetrics,
        metrics_pb2.Metrics,
        slice_budgets_pb2.SliceBudgets,
        slice_metrics_pb2.SliceMetrics,
    )
    for message_type in messages:
        original = message_type()
        encoded = original.SerializeToString()
        decoded = message_type()
        decoded.ParseFromString(encoded)
        if decoded != original:
            raise RuntimeError(f"round-trip failed for {message_type.__name__}")

    importlib.import_module("edgeric_messenger")
    importlib.import_module("send_mcs")
    importlib.import_module("send_weight")

    figure = plt.figure(figsize=(1, 1))
    output = io.BytesIO()
    figure.savefig(output, format="pdf")
    plt.close(figure)
    if not output.getvalue().startswith(b"%PDF"):
        raise RuntimeError("Matplotlib in-memory PDF export failed")

    subprocess.run(
        [
            sys.executable,
            "-I",
            "-B",
            "-c",
            (
                "import sys; sys.path.insert(0, sys.argv[1]); "
                "import metrics_pb2, slice_metrics_pb2, slice_budgets_pb2; "
                "assert metrics_pb2.Metrics().SerializeToString() == b''"
            ),
            str(protobuf_dir),
        ],
        check=True,
        cwd=protobuf_dir,
    )

    if args.with_muapps:
        for module_name in (
            "gym",
            "hydra",
            "kaleido",
            "pandas",
            "PIL.Image",
            "plotly.express",
            "ray.rllib",
            "redis",
            "scipy.optimize",
            "torch",
            "stream_rl",
        ):
            importlib.import_module(module_name)

        import plotly.graph_objects as go
        import plotly.io as pio

        png = pio.to_image(go.Figure(), format="png", width=64, height=64)
        if not png.startswith(b"\x89PNG\r\n\x1a\n"):
            raise RuntimeError("Plotly/Kaleido in-memory PNG export failed")

    print(f"Python: {sys.version.split()[0]} ({sys.executable})")
    print(f"Python Protobuf runtime: {google.protobuf.__version__}")
    print(f"Matplotlib: {matplotlib.__version__}")
    print(f"NumPy: {numpy.__version__}")
    print(f"PyZMQ: {zmq.__version__}; libzmq: {zmq.zmq_version()}")
    print("EdgeRIC import and Protobuf round-trip smoke check: PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
