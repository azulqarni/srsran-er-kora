#!/usr/bin/env python3
"""Plot commanded PRBs and live SLA adherence from muApp5 CSV logs."""

from __future__ import annotations

import argparse
import csv
import re
import sys
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np


DEFAULT_SLOT_DURATION_S = 0.0005
LINE_WIDTH = 1.1
LEGEND_SIZE = "x-small"
LOG_DIR_CANDIDATES = (
    Path(__file__).resolve().parent / "logs",
    Path("/tmp/edgeric_muapp5_logs"),
)
COUNTER_COLUMNS = {
    "wall_time",
    "rnti",
    "slice_id",
    "start_slot",
    "end_slot",
    "start_tti",
    "end_tti",
    "tx_bytes_delta",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Plot commanded PRBs and live SLA adherence."
    )
    parser.add_argument(
        "--block-log-file",
        help="Block CSV to plot; defaults to the latest compatible local log.",
    )
    parser.add_argument(
        "--log-timestamp",
        help="Select an auto-discovered run by its integer timestamp suffix.",
    )
    parser.add_argument(
        "--out-prefix",
        default="kpi_ktti_churn",
        help="Path prefix for the generated PDF (default: %(default)s).",
    )
    parser.add_argument(
        "--start-block-skip",
        type=int,
        default=0,
        help="Skip this many completed blocks (default: %(default)s).",
    )
    parser.add_argument(
        "--slot-duration-s",
        type=float,
        default=DEFAULT_SLOT_DURATION_S,
        help="Radio-slot duration used for live throughput (default: %(default)s).",
    )
    args = parser.parse_args()
    if args.start_block_skip < 0:
        parser.error("--start-block-skip must be nonnegative")
    if not np.isfinite(args.slot_duration_s) or args.slot_duration_s <= 0.0:
        parser.error("--slot-duration-s must be finite and positive")
    return args


def extract_slice_ids(fieldnames: Sequence[str]) -> List[int]:
    pattern = re.compile(r"^prb_avg_s(\d+)$")
    return sorted(
        int(match.group(1))
        for name in fieldnames
        if (match := pattern.match(name)) is not None
    )


def missing_block_columns(fieldnames: Sequence[str]) -> List[str]:
    available = set(fieldnames)
    missing = [
        name
        for name in ("block_idx", "block_start_slot", "block_end_slot", "total_prbs")
        if name not in available
    ]
    slice_ids = extract_slice_ids(fieldnames)
    if not slice_ids:
        missing.append("prb_avg_s<slice-id>")
    for sid in slice_ids:
        for field in ("prb_avg", "d", "x_next", "x_floor"):
            name = f"{field}_s{sid}"
            if name not in available:
                missing.append(name)
    return missing


def block_log_status(path: Path) -> Tuple[bool, bool]:
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            compatible = not missing_block_columns(reader.fieldnames or [])
            return compatible, compatible and next(reader, None) is not None
    except (OSError, csv.Error):
        return False, False


def is_compatible_counter_log(path: Path) -> bool:
    try:
        with path.open(newline="") as handle:
            reader = csv.DictReader(handle)
            return COUNTER_COLUMNS <= set(reader.fieldnames or [])
    except (OSError, csv.Error):
        return False


def resolve_block_log(explicit: Optional[str], timestamp: Optional[str]) -> Optional[Path]:
    if explicit:
        candidate = Path(explicit).expanduser()
        return candidate if candidate.is_file() else None

    pattern = (
        f"oco_cqi_slicing_ktti_churn_blocks_*_{timestamp}.csv"
        if timestamp
        else "oco_cqi_slicing_ktti_churn_blocks_*_*.csv"
    )
    candidates: List[Path] = []
    for log_dir in LOG_DIR_CANDIDATES:
        if log_dir.is_dir():
            candidates.extend(log_dir.glob(pattern))
    empty_candidate: Optional[Path] = None
    for candidate in sorted(
        set(candidates), key=lambda path: path.stat().st_mtime, reverse=True
    ):
        compatible, has_rows = block_log_status(candidate)
        counter_log = infer_counter_log(candidate)
        if (
            compatible
            and counter_log is not None
            and is_compatible_counter_log(counter_log)
        ):
            if has_rows:
                return candidate
            if empty_candidate is None:
                empty_candidate = candidate
    return empty_candidate


def infer_counter_log(block_log: Path) -> Optional[Path]:
    if "_blocks_" not in block_log.name:
        return None
    candidate = block_log.with_name(
        block_log.name.replace("_blocks_", "_counter_intervals_", 1)
    )
    return candidate if candidate.is_file() else None


def finite_float(value, label: str) -> float:
    try:
        result = float(str(value).strip())
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} is not numeric") from exc
    if not np.isfinite(result):
        raise ValueError(f"{label} must be finite")
    return result


def integer_value(value, label: str) -> int:
    number = finite_float(value, label)
    if not number.is_integer():
        raise ValueError(f"{label} must be an integer")
    return int(number)


def round_min_prbs(
    allocation: Sequence[float], floor: Sequence[float], total_prbs: int
) -> np.ndarray:
    x = np.asarray(allocation, dtype=float)
    minimum_share = np.asarray(floor, dtype=float)
    if (
        x.shape != minimum_share.shape
        or x.ndim != 1
        or total_prbs <= 0
        or not np.all(np.isfinite(x))
        or not np.all(np.isfinite(minimum_share))
    ):
        raise ValueError("invalid allocation data for PRB rounding")
    real = np.clip(x, 0.0, None) * total_prbs
    rounded = np.rint(real).astype(int)
    minimum = np.rint(np.clip(minimum_share, 0.0, None) * total_prbs).astype(int)
    if total_prbs >= len(minimum):
        minimum = np.where(minimum_share > 0.0, np.maximum(minimum, 1), 0)
    else:
        minimum = np.zeros_like(minimum)
    rounded = np.maximum(rounded, minimum)
    overflow = max(int(np.sum(rounded)) - total_prbs, 0)
    while overflow > 0:
        candidates = np.flatnonzero(rounded > minimum)
        if not candidates.size:
            break
        index = int(candidates[np.argmax((rounded - real)[candidates])])
        rounded[index] -= 1
        overflow -= 1
    if overflow or np.any(rounded < minimum) or int(np.sum(rounded)) > total_prbs:
        raise ValueError("PRB rounding could not satisfy the cell budget")
    return rounded


def load_block_log(
    path: Path,
) -> Tuple[
    List[int],
    np.ndarray,
    np.ndarray,
    np.ndarray,
    Dict[int, np.ndarray],
    Dict[int, np.ndarray],
    Dict[int, np.ndarray],
]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        fieldnames = reader.fieldnames or []
        missing = missing_block_columns(fieldnames)
        if missing:
            raise ValueError(
                f"incompatible block CSV; missing columns: {', '.join(missing)}"
            )
        rows = list(reader)
    if not rows:
        raise ValueError(
            "block CSV has no completed data rows; the controller did not finish "
            "a K-sample block"
        )

    slice_ids = extract_slice_ids(fieldnames)
    blocks: List[int] = []
    starts: List[int] = []
    ends: List[int] = []
    prbs: Dict[int, List[float]] = {sid: [] for sid in slice_ids}
    slas: Dict[int, List[float]] = {sid: [] for sid in slice_ids}
    commanded: Dict[int, List[float]] = {sid: [] for sid in slice_ids}
    command_columns = [f"commanded_min_prb_s{sid}" for sid in slice_ids]
    command_presence = [name in fieldnames for name in command_columns]
    if any(command_presence) and not all(command_presence):
        raise ValueError("block CSV has incomplete commanded-minimum columns")
    has_logged_commands = all(command_presence)

    for row_number, row in enumerate(rows, start=2):
        blocks.append(integer_value(row.get("block_idx"), f"row {row_number} block_idx"))
        starts.append(
            integer_value(row.get("block_start_slot"), f"row {row_number} block_start_slot")
        )
        ends.append(
            integer_value(row.get("block_end_slot"), f"row {row_number} block_end_slot")
        )
        total_prbs = integer_value(row.get("total_prbs"), f"row {row_number} total_prbs")
        x_next = np.asarray([
            finite_float(row.get(f"x_next_s{sid}"), f"row {row_number} x_next_s{sid}")
            for sid in slice_ids
        ])
        x_floor = np.asarray([
            finite_float(row.get(f"x_floor_s{sid}"), f"row {row_number} x_floor_s{sid}")
            for sid in slice_ids
        ])
        calculated_command = round_min_prbs(x_next, x_floor, total_prbs)
        if has_logged_commands:
            logged_command = np.asarray([
                integer_value(
                    row.get(f"commanded_min_prb_s{sid}"),
                    f"row {row_number} commanded_min_prb_s{sid}",
                )
                for sid in slice_ids
            ])
            if not np.array_equal(logged_command, calculated_command):
                raise ValueError(f"row {row_number} commanded minima disagree with x_next")
            calculated_command = logged_command

        for index, sid in enumerate(slice_ids):
            prb = finite_float(
                row.get(f"prb_avg_s{sid}"), f"row {row_number} prb_avg_s{sid}"
            )
            sla = finite_float(row.get(f"d_s{sid}"), f"row {row_number} d_s{sid}")
            if prb < 0.0:
                raise ValueError(f"row {row_number} prb_avg_s{sid} must be nonnegative")
            if sla <= 0.0:
                raise ValueError(f"row {row_number} d_s{sid} must be positive")
            prbs[sid].append(prb)
            slas[sid].append(sla)
            commanded[sid].append(float(calculated_command[index]))

    block_array = np.asarray(blocks, dtype=float)
    start_array = np.asarray(starts, dtype=np.int64)
    end_array = np.asarray(ends, dtype=np.int64)
    if np.any(start_array < 0) or np.any(end_array <= start_array):
        raise ValueError("block slot windows must be nonnegative and nonempty")
    if np.any(start_array[1:] < end_array[:-1]):
        raise ValueError("block slot windows must not overlap")
    if np.any(block_array[1:] <= block_array[:-1]):
        raise ValueError("block_idx must increase strictly")

    return (
        slice_ids,
        block_array,
        start_array,
        end_array,
        {sid: np.asarray(values, dtype=float) for sid, values in prbs.items()},
        {sid: np.asarray(values, dtype=float) for sid, values in slas.items()},
        {sid: np.asarray(values, dtype=float) for sid, values in commanded.items()},
    )


def load_counter_intervals(
    path: Path, controlled_slices: Sequence[int]
) -> List[Tuple[int, int, int, float]]:
    with path.open(newline="") as handle:
        reader = csv.DictReader(handle)
        missing = sorted(COUNTER_COLUMNS - set(reader.fieldnames or []))
        if missing:
            raise ValueError(
                f"incompatible counter-interval CSV; missing columns: {', '.join(missing)}"
            )
        rows = list(reader)

    controlled = set(controlled_slices)
    intervals: List[Tuple[int, int, int, int, float]] = []
    for row_number, row in enumerate(rows, start=2):
        rnti = integer_value(row.get("rnti"), f"row {row_number} rnti")
        sid = integer_value(row.get("slice_id"), f"row {row_number} slice_id")
        start = integer_value(row.get("start_slot"), f"row {row_number} start_slot")
        end = integer_value(row.get("end_slot"), f"row {row_number} end_slot")
        integer_value(row.get("start_tti"), f"row {row_number} start_tti")
        integer_value(row.get("end_tti"), f"row {row_number} end_tti")
        delta = finite_float(
            row.get("tx_bytes_delta"), f"row {row_number} tx_bytes_delta"
        )
        if rnti < 0 or sid < 0:
            raise ValueError(f"row {row_number} RNTI and slice ID must be nonnegative")
        if start < 0 or end <= start:
            raise ValueError(f"row {row_number} has an invalid counter interval")
        if delta < 0.0:
            raise ValueError(f"row {row_number} tx_bytes_delta must be nonnegative")
        if sid in controlled:
            intervals.append((rnti, sid, start, end, delta))

    last_end: Dict[int, int] = {}
    for rnti, _sid, start, end, _delta in sorted(
        intervals, key=lambda item: (item[0], item[2], item[3])
    ):
        if start < last_end.get(rnti, start):
            raise ValueError(f"counter intervals overlap for RNTI {rnti}")
        last_end[rnti] = end
    return [(sid, start, end, delta) for _rnti, sid, start, end, delta in intervals]


def rebin_counter_intervals(
    slice_ids: Sequence[int],
    starts: np.ndarray,
    ends: np.ndarray,
    intervals: Sequence[Tuple[int, int, int, float]],
) -> Dict[int, np.ndarray]:
    bytes_by_slice = {sid: np.zeros(len(starts), dtype=float) for sid in slice_ids}
    for sid, start, end, delta in intervals:
        overlap = np.maximum(
            0.0,
            np.minimum(float(end), ends) - np.maximum(float(start), starts),
        )
        bytes_by_slice[sid] += float(delta) * overlap / float(end - start)
    return bytes_by_slice


def normalized_sla_adherence(
    slice_ids: Sequence[int],
    starts: np.ndarray,
    ends: np.ndarray,
    bytes_by_slice: Dict[int, np.ndarray],
    slas: Dict[int, np.ndarray],
    slot_duration_s: float,
) -> Dict[int, np.ndarray]:
    durations = (ends - starts).astype(float) * slot_duration_s
    if np.any(durations <= 0.0):
        raise ValueError("block durations must be positive")
    return {
        sid: 8.0 * bytes_by_slice[sid] / durations / slas[sid]
        for sid in slice_ids
    }


def plot_kpis(
    slice_ids: Sequence[int],
    blocks: np.ndarray,
    prbs: Dict[int, np.ndarray],
    commanded: Dict[int, np.ndarray],
    adherence: Dict[int, np.ndarray],
    out_prefix: str,
    start_skip: int,
) -> Optional[str]:
    block_mask = blocks >= float(start_skip)
    block_axis = blocks[block_mask] - float(start_skip)
    command_blocks = np.concatenate(([blocks[0]], blocks + 1.0))
    command_mask = command_blocks >= float(start_skip)
    command_axis = command_blocks[command_mask] - float(start_skip)
    if not block_axis.size and not command_axis.size:
        return None

    fig, (ax_prb, ax_sla) = plt.subplots(2, 1, sharex=True, figsize=(7.0, 6.0))
    colors = {sid: f"C{index % 10}" for index, sid in enumerate(slice_ids)}
    for sid in slice_ids:
        command_values = np.concatenate(([prbs[sid][0]], commanded[sid]))
        if command_axis.size:
            command_y = command_values[command_mask]
            ax_prb.step(
                np.append(command_axis, command_axis[-1] + 1.0),
                np.append(command_y, command_y[-1]),
                where="post",
                label=f"slice {sid}",
                color=colors[sid],
                linewidth=LINE_WIDTH,
            )
        if block_axis.size:
            adherence_y = adherence[sid][block_mask]
            ax_sla.step(
                np.append(block_axis, block_axis[-1] + 1.0),
                np.append(adherence_y, adherence_y[-1]),
                where="post",
                label=f"slice {sid}",
                color=colors[sid],
                linewidth=LINE_WIDTH,
            )

    ax_prb.set_ylabel("PRB share")
    ax_prb.margins(x=0)
    ax_prb.legend(fontsize=LEGEND_SIZE)
    ax_sla.axhline(
        1.0, color="black", linestyle="--", linewidth=0.9, label="SLA target"
    )
    ax_sla.set_xlabel("Block index")
    ax_sla.set_ylabel("SLA adherence")
    ax_sla.margins(x=0)
    ax_sla.legend(fontsize=LEGEND_SIZE, ncol=2)
    fig.tight_layout()
    path = f"{out_prefix}.pdf"
    fig.savefig(path)
    plt.close(fig)
    return path


def main() -> None:
    args = parse_args()
    block_log = resolve_block_log(args.block_log_file, args.log_timestamp)
    if block_log is None:
        raise SystemExit("error: no compatible muApp5 block log with slot bounds found")
    counter_log = infer_counter_log(block_log)
    if counter_log is None:
        raise SystemExit(
            "error: matching counter-interval log required for live SLA adherence; "
            "run the updated controller"
        )

    try:
        slice_ids, blocks, starts, ends, prbs, slas, commanded = load_block_log(block_log)
        intervals = load_counter_intervals(counter_log, slice_ids)
        bytes_by_slice = rebin_counter_intervals(slice_ids, starts, ends, intervals)
        adherence = normalized_sla_adherence(
            slice_ids, starts, ends, bytes_by_slice, slas, args.slot_duration_s
        )
    except (OSError, csv.Error, ValueError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    out_prefix = str(Path(args.out_prefix).expanduser())
    Path(out_prefix).parent.mkdir(parents=True, exist_ok=True)
    output = plot_kpis(
        slice_ids, blocks, prbs, commanded, adherence,
        out_prefix, args.start_block_skip,
    )

    print(f"Using block log: {block_log}")
    print(f"Using counter-interval log: {counter_log}")
    print(f"Loaded completed blocks: {len(blocks)}")
    if len(blocks) < 2:
        print(
            "warning: only one completed block; SLA adherence has one sample",
            file=sys.stderr,
        )
    if output is not None:
        print(f"Saved: {output}")


if __name__ == "__main__":
    main()
