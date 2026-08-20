#!/usr/bin/env python3
import csv
import hashlib
import json
import math
import os
import sys
import time
from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Dict, List, Optional, Sequence, Tuple

import numpy as np

try:
    import zmq
except ModuleNotFoundError:  # Mathematical controller helpers can run without pyzmq.
    zmq = None

# Deployment configuration: edit these assignments, then restart the muApp.
SLICE_IDS = (2, 3, 4, 5, 6)
SLA_BPS = {2: 200e6, 3: 50e6, 4: 100e6, 5: 50e6, 6: 100e6}
K = 100
N_PRB = 273
B_PRB_HZ = 12 * 30_000
BETA = 1.30
GAMMA = 0.02
D = 9
T = 1000  # Calibration horizon for the fixed eta grid, in K-TTI blocks.
SLOT_DURATION_S = 0.0005
SE_UPPER_BOUND = {sid: 5.5547 for sid in SLICE_IDS}
INITIAL_ALLOCATION = np.array([0.40, 0.10, 0.20, 0.10, 0.20], dtype=float)
MIN_SHARE_TOTAL = 0.10
MIN_SHARE_TOTAL_CAP = 0.12
MIN_SHARE_MIN = 0.01
MIN_SHARE_MAX = 0.04
UE_METRICS_ENDPOINT = "ipc:///tmp/metrics"
SLICE_METRICS_ENDPOINT = "ipc:///tmp/slice_metrics"
SLICE_BUDGET_ENDPOINT = "ipc:///tmp/control_slice_budgets"
LOG_DIR = os.path.join(os.path.dirname(__file__), "logs")
PRIORITY = {sid: 10 for sid in SLICE_IDS}
MAX_PRB_WINDOW = 15_000
POLL_TIMEOUT_MS = 1000
LOG_ENABLED = True
LOG_TTI_ENABLED = False
CQI_LOG_ENABLED = False
TTI_MODULUS = 1 << 32

LOG_TAG_UNITS_SCALE = 1e6
SLA_WEIGHT_EXP = 0.90
SLA_WEIGHT_MIN = 0.5
SLA_WEIGHT_MAX = 2.5
EPS = 1e-12
LOG_SCHEMA_VERSION = 2

CQI_TO_SE_NR = np.array([
    0.0, 0.1523, 0.2344, 0.3770, 0.6016, 0.8770, 1.1758, 1.4766,
    1.9141, 2.4063, 2.7305, 3.3223, 3.9023, 4.5234, 5.1152, 5.5547,
], dtype=float)

def sla_utility_weights() -> np.ndarray:
    """Return mean-normalized SLA-size weights in slice-ID order."""
    sla = np.array([SLA_BPS[sid] for sid in SLICE_IDS], dtype=float)
    if not np.all(np.isfinite(sla)) or np.any(sla <= 0.0):
        raise ValueError("SLA targets must be finite and positive")
    weights = np.power(sla / float(np.mean(sla)), SLA_WEIGHT_EXP)
    weights = np.clip(weights, SLA_WEIGHT_MIN, SLA_WEIGHT_MAX)
    return weights / float(np.mean(weights))

SLA_WEIGHTS = sla_utility_weights()

def allocation_floor() -> np.ndarray:
    """Return the SLA-weighted anti-starvation floor in slice-ID order."""
    sla = np.array([SLA_BPS[sid] for sid in SLICE_IDS], dtype=float)
    sla_total = float(np.sum(sla))
    if not np.all(np.isfinite(sla)) or sla_total <= 0.0:
        raise ValueError("SLA targets must have a finite positive sum")
    weights = sla / sla_total
    floor = np.clip(MIN_SHARE_TOTAL * weights, MIN_SHARE_MIN, MIN_SHARE_MAX)
    floor_sum = float(np.sum(floor))
    if floor_sum > MIN_SHARE_TOTAL_CAP:
        floor *= MIN_SHARE_TOTAL_CAP / floor_sum
    return floor

MIN_ALLOCATION = allocation_floor()

def validate_configuration() -> None:
    """Reject invalid configuration before creating sockets or sending budgets."""
    if len(set(SLICE_IDS)) != len(SLICE_IDS) or not SLICE_IDS:
        raise ValueError("SLICE_IDS must be nonempty and unique")
    if set(SLICE_IDS) & {0, 1}:
        raise ValueError("built-in slices 0 and 1 must stay outside the learned share vector")
    if set(SLA_BPS) != set(SLICE_IDS) or set(SE_UPPER_BOUND) != set(SLICE_IDS):
        raise ValueError("SLA_BPS and SE_UPPER_BOUND must cover exactly SLICE_IDS")
    numeric = (
        B_PRB_HZ, SLOT_DURATION_S, BETA, GAMMA, SLA_WEIGHT_EXP, SLA_WEIGHT_MIN,
        SLA_WEIGHT_MAX, *SLA_BPS.values(), *SE_UPPER_BOUND.values(),
    )
    if not all(math.isfinite(value) for value in numeric):
        raise ValueError("numeric configuration values must be finite")
    if any(not isinstance(value, int) or value <= 0 for value in (K, N_PRB, D, T)):
        raise ValueError("K, N_PRB, D, and T must be positive")
    if B_PRB_HZ <= 0 or SLOT_DURATION_S <= 0.0 or BETA <= 0.0 or GAMMA < 0.0:
        raise ValueError(
            "B_PRB_HZ/SLOT_DURATION_S/BETA must be positive and GAMMA nonnegative"
        )
    if not isinstance(LOG_SCHEMA_VERSION, int) or LOG_SCHEMA_VERSION <= 0:
        raise ValueError("LOG_SCHEMA_VERSION must be a positive integer")
    if (
        SLA_WEIGHT_EXP < 0.0
        or SLA_WEIGHT_MIN <= 0.0
        or SLA_WEIGHT_MAX < SLA_WEIGHT_MIN
        or len(SLA_WEIGHTS) != len(SLICE_IDS)
        or not np.all(np.isfinite(SLA_WEIGHTS))
    ):
        raise ValueError("SLA utility weights are invalid")
    if any(SLA_BPS[sid] <= 0.0 or SE_UPPER_BOUND[sid] < 0.0 for sid in SLICE_IDS):
        raise ValueError("SLA targets must be positive and SE bounds nonnegative")
    if len(INITIAL_ALLOCATION) != len(SLICE_IDS):
        raise ValueError("INITIAL_ALLOCATION length must match SLICE_IDS")
    if not 0.0 <= MIN_SHARE_TOTAL <= MIN_SHARE_TOTAL_CAP <= 1.0:
        raise ValueError("minimum-share totals must satisfy 0 <= total <= cap <= 1")
    if MIN_SHARE_MIN < 0.0 or MIN_SHARE_MAX < MIN_SHARE_MIN:
        raise ValueError("minimum-share bounds are invalid")
    if len(MIN_ALLOCATION) != len(SLICE_IDS) or float(np.sum(MIN_ALLOCATION)) >= 1.0:
        raise ValueError("minimum allocation must leave a positive residual budget")
    if not is_feasible(INITIAL_ALLOCATION):
        raise ValueError("INITIAL_ALLOCATION is outside the feasible allocation set")

def _finite_vector(value: Sequence[float], name: str) -> np.ndarray:
    arr = np.asarray(value, dtype=float)
    if arr.ndim != 1 or not np.all(np.isfinite(arr)):
        raise ValueError(f"{name} must be a finite one-dimensional vector")
    return arr

def _forward_tti_delta(tti: int, previous_tti: Optional[int]) -> int:
    """Return a forward uint32 TTI distance, or -1 for stale/duplicate input."""
    if previous_tti is None:
        return 0
    delta = (int(tti) - int(previous_tti)) % TTI_MODULUS
    return delta if 0 < delta < TTI_MODULUS // 2 else -1

def _counter_delta_and_slots(
    current_counter: float,
    previous_counter: float,
    current_tti: int,
    previous_tti: int,
) -> Tuple[float, int]:
    """Return a valid cumulative-counter delta and its RNTI observation span."""
    values = (current_counter, previous_counter)
    if not all(math.isfinite(value) and value >= 0.0 for value in values):
        return 0.0, 0
    counter_slots = _forward_tti_delta(current_tti, previous_tti)
    raw_delta = float(current_counter) - float(previous_counter)
    if counter_slots <= 0 or raw_delta < 0.0:
        return 0.0, 0
    return raw_delta, counter_slots

def _counter_interval_and_baseline(
    current_counter: float,
    current_tti: int,
    current_slice: int,
    previous_sample: Optional[Tuple[float, int, int]],
) -> Tuple[float, int, Optional[Tuple[float, int, int]]]:
    """Keep an interval open across sparse reports and float-counter plateaus."""
    if not math.isfinite(current_counter) or current_counter < 0.0:
        return 0.0, 0, previous_sample
    current_sample = (float(current_counter), int(current_tti), int(current_slice))
    if previous_sample is None:
        return 0.0, 0, current_sample

    previous_counter, previous_tti, previous_slice = previous_sample
    if previous_slice != current_slice or current_counter < previous_counter:
        return 0.0, 0, current_sample

    raw_delta, interval_slots = _counter_delta_and_slots(
        current_counter, previous_counter, current_tti, previous_tti
    )
    if raw_delta > 0.0:
        return raw_delta, interval_slots, current_sample
    return 0.0, 0, previous_sample

def is_feasible(x: Sequence[float], tolerance: float = 1e-10) -> bool:
    arr = np.asarray(x, dtype=float)
    return bool(
        arr.ndim == 1
        and len(arr) == len(MIN_ALLOCATION)
        and np.all(np.isfinite(arr))
        and np.all(arr >= MIN_ALLOCATION - tolerance)
        and float(np.sum(arr)) <= 1.0 + tolerance
    )

def project_allocation(value: Sequence[float]) -> np.ndarray:
    """Project onto the floor-constrained slack simplex."""
    v = _finite_vector(value, "allocation")
    if len(v) != len(MIN_ALLOCATION):
        raise ValueError("allocation has the wrong length")
    residual_budget = 1.0 - float(np.sum(MIN_ALLOCATION))
    residual = np.maximum(v - MIN_ALLOCATION, 0.0)
    if float(np.sum(residual)) <= residual_budget:
        return MIN_ALLOCATION + residual

    shifted = v - MIN_ALLOCATION
    ordered = np.sort(shifted)[::-1]
    cumulative = np.cumsum(ordered)
    candidates = np.nonzero(
        ordered - (cumulative - residual_budget) /
        (np.arange(len(v), dtype=float) + 1.0) > 0.0
    )[0]
    if candidates.size == 0:
        return MIN_ALLOCATION.copy()
    rho = int(candidates[-1])
    threshold = float((cumulative[rho] - residual_budget) / (rho + 1))
    projected = MIN_ALLOCATION + np.maximum(shifted - threshold, 0.0)
    if not is_feasible(projected):
        raise FloatingPointError("simplex projection produced an infeasible allocation")
    return projected

def gradient_bound_and_step_sizes(n_prb: int = N_PRB) -> Tuple[float, np.ndarray]:
    """Return the weighted gradient bound and base eta expert step sizes."""
    se_bar = np.array([SE_UPPER_BOUND[sid] for sid in SLICE_IDS], dtype=float)
    theta = np.array([SLA_BPS[sid] for sid in SLICE_IDS], dtype=float)
    diameter = math.sqrt(2.0)
    gradient_bound = (
        BETA * float(n_prb) * B_PRB_HZ / math.log1p(BETA)
    ) * float(np.sqrt(np.sum((SLA_WEIGHTS * se_bar / theta) ** 2)))
    etas = np.array([
        (2.0 ** index) * diameter / ((gradient_bound + GAMMA) * math.sqrt(T))
        for index in range(D)
    ], dtype=float)
    return gradient_bound, etas

def block_utility_and_gradient(
    allocation: Sequence[float],
    se_samples: Sequence[Sequence[float]],
    n_prb: int = N_PRB,
) -> Tuple[float, np.ndarray, np.ndarray, Dict[int, Dict[str, float]]]:
    """Evaluate normalized SLA utility and its analytic block gradient."""
    x = _finite_vector(allocation, "allocation")
    samples = np.asarray(se_samples, dtype=float)
    if samples.ndim != 2 or samples.shape[1] != len(SLICE_IDS) or samples.shape[0] == 0:
        raise ValueError("se_samples must have shape (TTIs, number of slices)")
    if len(x) != len(SLICE_IDS) or not is_feasible(x):
        raise ValueError("allocation is outside the feasible allocation set")
    if not np.all(np.isfinite(samples)) or np.any(samples < 0.0):
        raise ValueError("spectral-efficiency samples must be finite and nonnegative")

    theta = np.array([SLA_BPS[sid] for sid in SLICE_IDS], dtype=float)
    # Each column is one controlled slice. Evaluate the nonlinear objective at
    # every accepted TTI, then take the block average from Eq. (5).
    a = float(n_prb) * B_PRB_HZ * samples / theta[np.newaxis, :]
    denominator = math.log1p(BETA)
    utility_samples = (
        SLA_WEIGHTS[np.newaxis, :]
        * np.log1p(BETA * a * x[np.newaxis, :])
        / denominator
    )
    gradient_samples = (
        SLA_WEIGHTS[np.newaxis, :]
        * BETA
        * a
        / (denominator * (1.0 + BETA * a * x[np.newaxis, :]))
    )
    per_slice_utility = np.mean(utility_samples, axis=0)
    gradient = np.mean(gradient_samples, axis=0)

    diagnostics: Dict[int, Dict[str, float]] = {}
    for index, sid in enumerate(SLICE_IDS):
        se_column = samples[:, index]
        c_value = float(n_prb) * B_PRB_HZ * float(np.mean(se_column))
        diagnostics[sid] = {
            "c": c_value,
            "d": float(SLA_BPS[sid]),
            "se_mean": float(np.mean(se_column)),
            "se_median": float(np.median(se_column)),
            "se_used": float(np.mean(se_column)),
            "served_bps_model": float(x[index] * c_value),
            "sla_weight": float(SLA_WEIGHTS[index]),
            "rate_over_sla": float(x[index] * c_value / SLA_BPS[sid]),
            "utility": float(per_slice_utility[index]),
            "grad": float(gradient[index]),
        }
    return float(np.sum(per_slice_utility)), gradient, per_slice_utility, diagnostics

def hedge_weights(weights: Sequence[float], losses: Sequence[float]) -> np.ndarray:
    """Update Hedge weights using stable log-space normalization."""
    w = _finite_vector(weights, "weights")
    loss = _finite_vector(losses, "losses")
    if len(w) != len(loss) or np.any(w < 0.0) or float(np.sum(w)) <= 0.0:
        raise ValueError("weights/losses are incompatible")
    w = w / float(np.sum(w))
    log_weights = np.log(np.clip(w, np.finfo(float).tiny, None)) - BETA * loss
    log_weights -= float(np.max(log_weights))
    updated = np.exp(log_weights)
    updated /= float(np.sum(updated))
    if not np.all(np.isfinite(updated)):
        raise FloatingPointError("Hedge normalization failed")
    return updated

def expert_step(
    expert_positions: Sequence[Sequence[float]],
    previous_positions: Sequence[Sequence[float]],
    weights: Sequence[float],
    etas: Sequence[float],
    gradient: Sequence[float],
) -> Tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Update expert allocations and Hedge weights for one block."""
    positions = np.asarray(expert_positions, dtype=float)
    previous = np.asarray(previous_positions, dtype=float)
    w = _finite_vector(weights, "weights")
    eta = _finite_vector(etas, "etas")
    grad = _finite_vector(gradient, "gradient")
    expected_shape = (len(w), len(SLICE_IDS))
    if positions.shape != expected_shape or previous.shape != expected_shape:
        raise ValueError("expert position matrices have the wrong shape")
    if len(eta) != len(w) or len(grad) != len(SLICE_IDS):
        raise ValueError("expert step vectors have incompatible lengths")
    if not np.all(np.isfinite(positions)) or not np.all(np.isfinite(previous)):
        raise ValueError("expert positions must be finite")

    deployed = np.sum(w[:, np.newaxis] * positions, axis=0) / float(np.sum(w))
    gains = np.array([float(np.dot(grad, position - deployed)) for position in positions])
    churn = np.sum(np.abs(positions - previous), axis=1)
    losses = -gains + GAMMA * churn
    next_weights = hedge_weights(w, losses)
    next_positions = np.stack([
        project_allocation(position + step_size * grad)
        for position, step_size in zip(positions, eta)
    ])
    return next_positions, next_weights, losses, gains, churn, deployed

def deployed_mixture(weights: Sequence[float], positions: Sequence[Sequence[float]]) -> np.ndarray:
    w = _finite_vector(weights, "weights")
    experts = np.asarray(positions, dtype=float)
    if experts.shape != (len(w), len(SLICE_IDS)) or float(np.sum(w)) <= 0.0:
        raise ValueError("invalid expert mixture")
    allocation = np.sum((w / float(np.sum(w)))[:, np.newaxis] * experts, axis=0)
    if not is_feasible(allocation):
        raise FloatingPointError("convex expert mixture is infeasible")
    return allocation

def shares_to_prbs(allocation: Sequence[float], total_prbs: int) -> Tuple[np.ndarray, np.ndarray]:
    """Round allocation shares to integer PRB budgets, repairing only overflow."""
    x = _finite_vector(allocation, "allocation")
    if not is_feasible(x) or total_prbs < 0:
        raise ValueError("cannot round an infeasible allocation or negative PRB budget")
    real = np.clip(x, 0.0, None) * int(total_prbs)
    rounded = np.rint(real).astype(int)
    minimum = np.rint(MIN_ALLOCATION * int(total_prbs)).astype(int)
    if total_prbs >= len(MIN_ALLOCATION):
        minimum = np.where(MIN_ALLOCATION > 0.0, np.maximum(minimum, 1), 0)
    else:
        minimum = np.zeros_like(minimum)
    rounded = np.maximum(rounded, minimum)
    overflow = max(int(np.sum(rounded)) - int(total_prbs), 0)
    while overflow > 0:
        candidates = np.flatnonzero(rounded > minimum)
        if candidates.size == 0:
            break
        excess = rounded[candidates] - real[candidates]
        index = int(candidates[int(np.argmax(excess))])
        rounded[index] -= 1
        overflow -= 1
    if overflow != 0 or np.any(rounded < 0) or int(np.sum(rounded)) > int(total_prbs):
        raise FloatingPointError("integer PRB rounding exceeded the available budget")
    return rounded, rounded.astype(float) - real

@dataclass
class SlicingController:
    """Maintain expert and meta-learner state across control blocks."""

    n_prb: int = N_PRB

    def __post_init__(self) -> None:
        self.gradient_bound, self.base_etas = gradient_bound_and_step_sizes(self.n_prb)
        x1 = project_allocation(INITIAL_ALLOCATION)
        self.positions = np.repeat(x1[np.newaxis, :], D, axis=0)
        self.previous_positions = self.positions.copy()
        self.weights = np.full(D, 1.0 / D, dtype=float)
        self.current_allocation = x1.copy()
        self.previous_allocation = x1.copy()
        self.block_index = 0
        self.last_utility: Optional[float] = None
        self.prediction_error_sq_cum = 0.0
        self.grad_history: deque = deque(maxlen=64)
        self.range_history: deque = deque(maxlen=64)
        self.gain_history: deque = deque(maxlen=64)
        self.churn_history: deque = deque(maxlen=64)

    def update_block(self, se_samples: Sequence[Sequence[float]]) -> Dict[str, object]:
        """Finish the current block and compute the next allocation."""
        if np.asarray(se_samples).shape != (K, len(SLICE_IDS)):
            raise ValueError(f"a controller update requires exactly {K} complete TTI samples")
        x_t = self.current_allocation.copy()
        x_prev = self.previous_allocation.copy()
        weights_before = self.weights.copy()
        positions_before = self.positions.copy()
        previous_positions = self.previous_positions.copy()
        utility, gradient, _, slice_stats = block_utility_and_gradient(
            x_t, se_samples, self.n_prb
        )
        applied_etas = self.base_etas

        next_positions, weights_after, losses, gains, churn, deployed = expert_step(
            positions_before,
            previous_positions,
            weights_before,
            applied_etas,
            gradient,
        )
        if not np.array_equal(deployed, x_t):
            if not np.allclose(deployed, x_t, rtol=0.0, atol=1e-14):
                raise FloatingPointError("controller state no longer equals the deployed expert mixture")
        x_next = deployed_mixture(weights_after, next_positions)

        meta_churn = float(np.sum(np.abs(x_t - x_prev)))
        switching_cost = GAMMA * meta_churn
        meta_step = x_next - x_t
        utility_delta = 0.0 if self.last_utility is None else utility - self.last_utility
        grad_norm = float(np.linalg.norm(gradient))
        self.prediction_error_sq_cum += grad_norm * grad_norm
        self.grad_history.append(grad_norm)
        self.range_history.append(float(np.max(losses) - np.min(losses)))
        self.gain_history.append(float(np.max(np.abs(gains))))
        self.churn_history.extend(float(value) for value in churn)
        gain_scale = max(float(np.quantile(self.gain_history, 0.90)), 1e-3)
        churn_scale = max(float(np.quantile(self.churn_history, 0.90)), 1e-2)

        eta_before = float(np.dot(weights_before, applied_etas))
        eta_after = float(np.dot(weights_after, applied_etas))
        state_eta_eff_next = float(np.dot(weights_after, self.base_etas))
        entropy = float(-np.sum(weights_after * np.log(np.clip(weights_after, np.finfo(float).tiny, None))))
        top = int(np.argmax(weights_after))
        records = []
        for index in range(D):
            records.append({
                "expert_id": index,
                "eta": float(applied_etas[index]),
                "gamma": GAMMA,
                "weight_before": float(weights_before[index]),
                "weight_after": float(weights_after[index]),
                "surrogate_loss": float(losses[index]),
                "linearized_utility_gain": float(gains[index]),
                "normalized_utility_gain": float(gains[index] / gain_scale),
                "expert_churn": float(churn[index]),
                "normalized_expert_churn": float(churn[index] / churn_scale),
                "x_distance_to_meta": float(np.linalg.norm(positions_before[index] - x_t)),
                "used_zero_churn_subgrad": int(churn[index] <= EPS),
            })

        result: Dict[str, object] = {
            "block_idx": self.block_index,
            "x_prev": x_prev,
            "x_t": x_t,
            "x_next": x_next,
            "block_utility": utility,
            "regularized_objective": utility - switching_cost,
            "utility_delta": utility_delta,
            "gradient": gradient,
            "slice_stats": slice_stats,
            "meta_churn": meta_churn,
            "switching_cost": switching_cost,
            "meta_step_l1": float(np.sum(np.abs(meta_step))),
            "meta_step_l2": float(np.linalg.norm(meta_step)),
            "losses": losses,
            "gains": gains,
            "expert_churn": churn,
            "weights_before": weights_before,
            "weights_after": weights_after,
            "eta_eff_before": eta_before,
            "eta_eff_after": eta_after,
            "state_eta_eff_next": state_eta_eff_next,
            "weights_entropy_after": entropy,
            "effective_num_experts": float(math.exp(entropy)),
            "upper_half_eta_weight": float(np.sum(weights_after[D // 2:])),
            "top_expert_id": top,
            "top_expert_weight": float(weights_after[top]),
            "top_expert_eta": float(applied_etas[top]),
            "expert_records": records,
            "gain_scale": gain_scale,
            "churn_scale": churn_scale,
            "range_t": float(np.max(losses) - np.min(losses)),
            "range_hat": float(np.quantile(self.range_history, 0.90)),
            "g_hat": float(np.quantile(self.grad_history, 0.90)),
            "grad_norm": grad_norm,
            "epsilon_sq_cum": self.prediction_error_sq_cum,
        }

        self.previous_positions = positions_before
        self.positions = next_positions
        self.weights = weights_after
        self.previous_allocation = x_t
        self.current_allocation = x_next
        self.last_utility = utility
        self.block_index += 1
        return result

class TelemetryBlock:
    """Collect exactly K valid, distinct metric TTIs under one allocation."""

    def __init__(self) -> None:
        self.last_tti: Optional[int] = None
        self.reset()

    def is_new_tti(self, tti: int) -> bool:
        return bool(tti > 0 and self._tti_delta(tti) >= 0)

    def _tti_delta(self, tti: int) -> int:
        return _forward_tti_delta(tti, self.last_tti)

    def add(
        self,
        tti: int,
        se_by_slice: Dict[int, float],
        served_bytes: Dict[int, float],
        slot_axis: Optional[int] = None,
    ) -> bool:
        if not self.is_new_tti(tti):
            return False
        elapsed = self._tti_delta(tti)
        self.last_tti = tti
        self.last_elapsed = elapsed
        self.logged_tti = tti
        self.slots_elapsed += elapsed
        if slot_axis is not None:
            end_slot = int(slot_axis)
            start_slot = end_slot - elapsed
            if start_slot < 0:
                raise ValueError("block slot coordinates must be nonnegative")
            if self.start_slot is None:
                self.start_slot = start_slot
            self.end_slot = end_slot
        self.se_rows.append([
            max(float(se_by_slice.get(sid, 0.0)), 0.0) for sid in SLICE_IDS
        ])
        for sid in SLICE_IDS:
            self.served_bytes[sid] += float(served_bytes.get(sid, 0.0))
        return True

    @property
    def ready(self) -> bool:
        return len(self.se_rows) == K

    def reset(self) -> None:
        self.se_rows: List[List[float]] = []
        self.served_bytes: Dict[int, float] = defaultdict(float)
        self.slots_elapsed = 0
        self.logged_tti: Optional[int] = None
        self.start_slot: Optional[int] = None
        self.end_slot: Optional[int] = None
        self.prb_sum = np.zeros(len(SLICE_IDS), dtype=float)
        self.max_prb_sum = np.zeros(len(SLICE_IDS), dtype=float)
        self.residual_sum = np.zeros(len(SLICE_IDS), dtype=float)
        self.residual_abs_max = 0.0
        self.last_elapsed = 0

BLOCK_GLOBAL_FIELDS = """
wall_time mode block_idx epoch_idx t_epoch epoch_len k_eff total_prbs update_rule
predictor optimistic_update rounding_mode min_share_mode min_share_value
min_share_total_cap min_share_min min_share_max baseline_mix_mode baseline_mix_rho
residual_budget beta_t range_t range_hat g_hat utility_grad_norm grad_norm_raw
grad_norm_clipped grad_clip block_utility utility_delta meta_churn meta_step_l1
meta_step_l2 epsilon_t prediction_cosine epsilon_sq_cum surrogate_gain_scale
surrogate_churn_scale gamma_base effective_num_experts upper_half_eta_weight
eta_eff_before gamma_eff_before eta_eff_after gamma_eff_after state_eta_eff_next
state_gamma_eff_next weights_entropy_after top_expert_id top_expert_weight
top_expert_eta top_expert_gamma used_zero_meta_subgrad epoch_reset
round_residual_sum round_residual_abs_max logged_tti block_start_slot block_end_slot
slots_elapsed_block block_update_ms
""".split()
BLOCK_SLICE_FIELDS = """
x_prev x x_next x_floor x_raw x_played x_next_raw share_minus_floor c d se_mean
se_median se_used served_bps_model sla_weight rate_over_sla utility grad prb_avg
max_prb_avg commanded_min_prb commanded_max_prb prb_residual_mean served_bytes_block
""".split()
EXPERT_HEADER = """
wall_time mode block_idx epoch_idx t_epoch expert_id eta gamma weight_before
weight_after surrogate_loss linearized_utility_gain normalized_utility_gain
expert_churn normalized_expert_churn x_distance_to_meta used_zero_churn_subgrad
""".split()
TTI_HEADER = """
wall_time tti block_idx slice_id x se_current se_running_mean min_prb max_prb
prb_residual total_prbs throughput_model_bps served_bytes_tti slots_elapsed exec_time_ms
""".split()
CQI_HEADER = """
wall_time tti rnti slice_id cqi snr tx_bytes dl_buffer ul_buffer
""".split()
COUNTER_INTERVAL_HEADER = """
wall_time rnti slice_id start_slot end_slot start_tti end_tti tx_bytes_delta
""".split()
OBJECTIVE_SAMPLE_HEADER = (
    "wall_time observation_block_idx controller_block_idx sample_idx tti slot "
    "slots_elapsed"
).split() + [f"se_s{sid}" for sid in SLICE_IDS]
OBJECTIVE_BLOCK_HEADER = (
    "wall_time observation_block_idx controller_block_idx update_status "
    "objective_valid update_error k_eff total_prbs block_start_slot block_end_slot "
    "slots_elapsed_block block_utility"
).split() + [f"x_s{sid}" for sid in SLICE_IDS] + [f"x_next_s{sid}" for sid in SLICE_IDS]

def block_log_header(slice_ids: Sequence[int] = SLICE_IDS) -> List[str]:
    header = list(BLOCK_GLOBAL_FIELDS)
    for sid in slice_ids:
        header.extend(f"{field}_s{sid}" for field in BLOCK_SLICE_FIELDS)
    return header

BLOCK_HEADER = block_log_header()

def _float_token(value: float) -> str:
    token = f"{float(value):.6f}".rstrip("0").rstrip(".") or "0"
    return token.replace(".", "p")

def _log_settings_tag() -> str:
    return "_".join([
        f"sb{_float_token(BETA)}",
        f"uu{_float_token(LOG_TAG_UNITS_SCALE)}",
        f"we{_float_token(SLA_WEIGHT_EXP)}",
        f"wn{_float_token(SLA_WEIGHT_MIN)}",
        f"wx{_float_token(SLA_WEIGHT_MAX)}",
    ])

def _source_sha256() -> str:
    with open(__file__, "rb") as source:
        return hashlib.sha256(source.read()).hexdigest()

def _run_metadata(run_id: str) -> Dict[str, object]:
    return {
        "schema_version": LOG_SCHEMA_VERSION,
        "run_id": run_id,
        "objective_id": "weighted_log_mean_over_accepted_samples_v1",
        "sample_weighting": "uniform_over_accepted_samples",
        "slice_ids": list(SLICE_IDS),
        "k": K,
        "n_prb_fallback": N_PRB,
        "b_prb_hz": B_PRB_HZ,
        "slot_duration_s": SLOT_DURATION_S,
        "beta": BETA,
        "gamma": GAMMA,
        "switching_norm": "l1",
        "switching_anchor": "initial_allocation",
        "allocation_sum_cap": 1.0,
        "sla_bps": {str(sid): SLA_BPS[sid] for sid in SLICE_IDS},
        "sla_weights": {str(sid): float(SLA_WEIGHTS[index]) for index, sid in enumerate(SLICE_IDS)},
        "allocation_floor": {str(sid): float(MIN_ALLOCATION[index]) for index, sid in enumerate(SLICE_IDS)},
        "initial_allocation": {str(sid): float(INITIAL_ALLOCATION[index]) for index, sid in enumerate(SLICE_IDS)},
        "missing_slice_se": "last_observation_carried_forward_from_cqi_1",
        "source_sha256": _source_sha256(),
    }

class CsvLogs:
    def __init__(self, mode: str = "live") -> None:
        os.makedirs(LOG_DIR, exist_ok=True)
        timestamp = time.time_ns()
        self.run_id = f"{mode}_{_log_settings_tag()}_{timestamp}"
        stem = f"{self.run_id}.csv"
        self.handles: Dict[str, object] = {}
        self.writers: Dict[str, object] = {}
        if LOG_ENABLED:
            self._open("block", f"oco_cqi_slicing_ktti_churn_blocks_{stem}", BLOCK_HEADER)
            self._open("expert", f"oco_cqi_slicing_ktti_churn_experts_{stem}", EXPERT_HEADER)
            self._open(
                "counter", f"oco_cqi_slicing_ktti_churn_counter_intervals_{stem}",
                COUNTER_INTERVAL_HEADER,
            )
            self._open(
                "objective_sample",
                f"oco_cqi_slicing_ktti_churn_objective_samples_{stem}",
                OBJECTIVE_SAMPLE_HEADER,
            )
            self._open(
                "objective_block",
                f"oco_cqi_slicing_ktti_churn_objective_blocks_{stem}",
                OBJECTIVE_BLOCK_HEADER,
            )
            metadata_path = os.path.join(
                LOG_DIR, f"oco_cqi_slicing_ktti_churn_run_metadata_{self.run_id}.json"
            )
            with open(metadata_path, "w", encoding="utf-8") as metadata_file:
                json.dump(_run_metadata(self.run_id), metadata_file, indent=2, sort_keys=True)
                metadata_file.write("\n")
        if LOG_TTI_ENABLED:
            self._open("tti", f"oco_cqi_slicing_ktti_churn_tti_{stem}", TTI_HEADER)
        if CQI_LOG_ENABLED:
            self._open("cqi", f"oco_cqi_slicing_ktti_churn_cqi_{stem}", CQI_HEADER)

    def _open(self, key: str, name: str, header: Sequence[str]) -> None:
        handle = open(os.path.join(LOG_DIR, name), "w", newline="")
        writer = csv.writer(handle)
        writer.writerow(header)
        self.handles[key] = handle
        self.writers[key] = writer

    def flush(self) -> None:
        for handle in self.handles.values():
            handle.flush()

    def close(self) -> None:
        self.flush()
        for handle in self.handles.values():
            handle.close()

def build_block_row(
    result: Dict[str, object],
    block: TelemetryBlock,
    total_prbs: int,
    block_update_ms: float,
    commanded_min_prbs: Sequence[int],
    commanded_max_prbs: Sequence[int],
) -> List[object]:
    gradient = np.asarray(result["gradient"], dtype=float)
    commanded_min = np.asarray(commanded_min_prbs, dtype=int)
    commanded_max = np.asarray(commanded_max_prbs, dtype=int)
    expected_shape = (len(SLICE_IDS),)
    if commanded_min.shape != expected_shape or commanded_max.shape != expected_shape:
        raise ValueError("commanded PRB vectors must match SLICE_IDS")
    if np.any(commanded_min < 0) or np.any(commanded_max < commanded_min):
        raise ValueError("commanded PRB bounds are invalid")
    values = {
        "wall_time": time.time(), "mode": "live", "block_idx": result["block_idx"],
        "epoch_idx": 0, "t_epoch": int(result["block_idx"]) + 1,
        "epoch_len": 1 << 30, "k_eff": len(block.se_rows), "total_prbs": float(total_prbs),
        "update_rule": "proj", "predictor": "none", "optimistic_update": 0,
        "rounding_mode": "deterministic", "min_share_mode": "sla",
        "min_share_value": MIN_SHARE_TOTAL,
        "min_share_total_cap": MIN_SHARE_TOTAL_CAP,
        "min_share_min": MIN_SHARE_MIN, "min_share_max": MIN_SHARE_MAX,
        "baseline_mix_mode": "none", "baseline_mix_rho": 0.0,
        "residual_budget": 1.0 - float(np.sum(MIN_ALLOCATION)),
        "beta_t": BETA, "range_t": result["range_t"],
        "range_hat": result["range_hat"], "g_hat": result["g_hat"],
        "utility_grad_norm": result["grad_norm"], "grad_norm_raw": result["grad_norm"],
        "grad_norm_clipped": result["grad_norm"], "grad_clip": max(1e-9, 4.0 * result["g_hat"]),
        "block_utility": result["block_utility"], "utility_delta": result["utility_delta"],
        "meta_churn": result["meta_churn"], "meta_step_l1": result["meta_step_l1"],
        "meta_step_l2": result["meta_step_l2"], "epsilon_t": result["grad_norm"],
        "prediction_cosine": 0.0, "epsilon_sq_cum": result["epsilon_sq_cum"],
        "surrogate_gain_scale": result["gain_scale"],
        "surrogate_churn_scale": result["churn_scale"], "gamma_base": GAMMA,
        "effective_num_experts": result["effective_num_experts"],
        "upper_half_eta_weight": result["upper_half_eta_weight"],
        "eta_eff_before": result["eta_eff_before"], "gamma_eff_before": GAMMA,
        "eta_eff_after": result["eta_eff_after"], "gamma_eff_after": GAMMA,
        "state_eta_eff_next": result["state_eta_eff_next"], "state_gamma_eff_next": GAMMA,
        "weights_entropy_after": result["weights_entropy_after"],
        "top_expert_id": result["top_expert_id"],
        "top_expert_weight": result["top_expert_weight"],
        "top_expert_eta": result["top_expert_eta"], "top_expert_gamma": GAMMA,
        "used_zero_meta_subgrad": int(result["meta_churn"] <= EPS), "epoch_reset": 0,
        "round_residual_sum": float(np.sum(block.residual_sum)),
        "round_residual_abs_max": block.residual_abs_max,
        "logged_tti": block.logged_tti if block.logged_tti is not None else "",
        "block_start_slot": block.start_slot if block.start_slot is not None else "",
        "block_end_slot": block.end_slot if block.end_slot is not None else "",
        "slots_elapsed_block": block.slots_elapsed, "block_update_ms": block_update_ms,
    }
    row: List[object] = [values[field] for field in BLOCK_GLOBAL_FIELDS]
    x_prev = np.asarray(result["x_prev"])
    x_t = np.asarray(result["x_t"])
    x_next = np.asarray(result["x_next"])
    stats = result["slice_stats"]
    for index, sid in enumerate(SLICE_IDS):
        stat = stats[sid]
        slice_values = {
            "x_prev": x_prev[index], "x": x_t[index], "x_next": x_next[index],
            "x_floor": MIN_ALLOCATION[index], "x_raw": x_t[index],
            "x_played": x_t[index], "x_next_raw": x_next[index],
            "share_minus_floor": x_t[index] - MIN_ALLOCATION[index],
            "c": stat["c"], "d": stat["d"], "se_mean": stat["se_mean"],
            "se_median": stat["se_median"], "se_used": stat["se_used"],
            "served_bps_model": stat["served_bps_model"],
            "sla_weight": stat["sla_weight"],
            "rate_over_sla": stat["rate_over_sla"], "utility": stat["utility"],
            "grad": gradient[index], "prb_avg": block.prb_sum[index] / len(block.se_rows),
            "max_prb_avg": block.max_prb_sum[index] / len(block.se_rows),
            "commanded_min_prb": commanded_min[index],
            "commanded_max_prb": commanded_max[index],
            "prb_residual_mean": block.residual_sum[index] / len(block.se_rows),
            "served_bytes_block": block.served_bytes[sid],
        }
        row.extend(slice_values[field] for field in BLOCK_SLICE_FIELDS)
    if len(row) != len(BLOCK_HEADER):
        raise AssertionError("block CSV row/header length mismatch")
    return row

def write_expert_rows(writer, result: Dict[str, object]) -> None:
    if writer is None:
        return
    now = time.time()
    for record in result["expert_records"]:
        values = {"wall_time": now, "mode": "live", "block_idx": result["block_idx"],
                  "epoch_idx": 0, "t_epoch": int(result["block_idx"]) + 1, **record}
        writer.writerow([values[field] for field in EXPERT_HEADER])

def slice_pf_se(cqis: Sequence[int], active: Sequence[bool]) -> float:
    if not cqis:
        return 0.0
    values = CQI_TO_SE_NR[np.clip(np.asarray(cqis, dtype=int), 0, len(CQI_TO_SE_NR) - 1)]
    if len(active) == len(values) and np.any(active):
        values = values[np.asarray(active, dtype=bool)]
    values = np.clip(values, 1e-9, None)
    return float(len(values) / np.sum(1.0 / values))

def _load_protobuf_modules():
    base_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    proto_dir = os.path.join(base_dir, "protobufs")
    if proto_dir not in sys.path:
        sys.path.insert(0, proto_dir)
    try:
        import metrics_pb2
        import slice_budgets_pb2
        import slice_metrics_pb2
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "generated Python Protobuf bindings are missing; run scripts/setup_edgeric_venv.sh"
        ) from exc
    return metrics_pb2, slice_budgets_pb2, slice_metrics_pb2

def _warn(message: str) -> None:
    print(f"warning: {message}", file=sys.stderr, flush=True)

def _append_builtin_slice_budgets(message, total_prbs: int) -> None:
    """Keep built-in slices outside the learned allocation vector."""
    if total_prbs <= 0:
        raise ValueError("total_prbs must be positive")

    srb_budget = message.budgets.add()
    srb_budget.slice_id = 0
    srb_budget.min_prb = 0
    srb_budget.max_prb = int(total_prbs)
    srb_budget.priority = 255

    default_drb_budget = message.budgets.add()
    default_drb_budget.slice_id = 1
    default_drb_budget.min_prb = 0
    default_drb_budget.max_prb = 0
    default_drb_budget.priority = 0

def _write_cqi_rows(writer, tti: int, ue_metrics) -> None:
    if writer is None:
        return
    now = time.time()
    for ue in ue_metrics:
        writer.writerow([
            now, tti if tti > 0 else "", ue.rnti, getattr(ue, "slice_id", 0),
            ue.cqi, ue.snr, ue.tx_bytes, ue.dl_buffer, ue.ul_buffer,
        ])

def _write_counter_interval(
    writer, rnti: int, slice_id: int, start_slot: int, end_slot: int,
    start_tti: int, end_tti: int, tx_bytes_delta: float,
) -> None:
    if writer is None or tx_bytes_delta <= 0.0 or end_slot <= start_slot:
        return
    writer.writerow([
        time.time(), rnti, slice_id, start_slot, end_slot,
        start_tti, end_tti, tx_bytes_delta,
    ])

def _write_objective_sample(
    writer,
    observation_block_idx: int,
    controller_block_idx: int,
    sample_idx: int,
    tti: int,
    slot: int,
    slots_elapsed: int,
    se_row: Sequence[float],
) -> None:
    if writer is None:
        return
    values = np.asarray(se_row, dtype=float)
    if values.shape != (len(SLICE_IDS),):
        raise ValueError("objective SE row must match SLICE_IDS")
    writer.writerow([
        time.time(), observation_block_idx, controller_block_idx, sample_idx,
        tti, slot, slots_elapsed, *values,
    ])

def _write_objective_block(
    writer,
    observation_block_idx: int,
    controller_block_idx: int,
    update_status: str,
    update_error: str,
    block: TelemetryBlock,
    total_prbs: int,
    played_allocation: Sequence[float],
    next_allocation: Sequence[float],
    block_result: Optional[Dict[str, object]],
) -> None:
    if writer is None:
        return
    played = np.asarray(played_allocation, dtype=float)
    next_x = np.asarray(next_allocation, dtype=float)
    expected = (len(SLICE_IDS),)
    if played.shape != expected or next_x.shape != expected:
        raise ValueError("objective action vectors must match SLICE_IDS")
    samples = np.asarray(block.se_rows, dtype=float)
    objective_valid = int(
        samples.shape == (K, len(SLICE_IDS))
        and np.all(np.isfinite(samples))
        and np.all(samples >= 0.0)
    )
    writer.writerow([
        time.time(), observation_block_idx, controller_block_idx, update_status,
        objective_valid, update_error, len(block.se_rows), total_prbs,
        block.start_slot if block.start_slot is not None else "",
        block.end_slot if block.end_slot is not None else "",
        block.slots_elapsed,
        block_result["block_utility"] if block_result is not None else "",
        *played, *next_x,
    ])


def _write_tti_rows(
    writer, tti: int, block_index: int, allocation: np.ndarray,
    se_by_slice: Dict[int, float], running_rows: Sequence[Sequence[float]],
    min_prbs: np.ndarray, max_prbs: np.ndarray, residuals: np.ndarray, total_prbs: int,
    served_bytes: Dict[int, float], slots_elapsed: int, exec_ms: float,
) -> None:
    if writer is None:
        return
    running = np.asarray(running_rows, dtype=float)
    now = time.time()
    for index, sid in enumerate(SLICE_IDS):
        se_value = float(se_by_slice.get(sid, 0.0))
        running_mean = float(np.mean(running[:, index])) if running.size else 0.0
        writer.writerow([
            now, tti if tti > 0 else "", block_index, sid, allocation[index], se_value,
            running_mean, int(min_prbs[index]), int(max_prbs[index]), residuals[index],
            float(total_prbs),
            allocation[index] * total_prbs * B_PRB_HZ * se_value,
            served_bytes.get(sid, 0.0), slots_elapsed, exec_ms,
        ])

def run_live() -> None:
    """Run the live RT-E2 loop; the allocation changes only after K distinct TTIs."""
    validate_configuration()
    if zmq is None:
        raise RuntimeError("pyzmq is missing; activate the repository EdgeRIC virtual environment")
    metrics_pb2, slice_budgets_pb2, slice_metrics_pb2 = _load_protobuf_modules()

    controller = SlicingController()
    block = TelemetryBlock()
    logs = CsvLogs("live")
    context = zmq.Context.instance()
    sub_ue = context.socket(zmq.SUB)
    sub_slice = context.socket(zmq.SUB)
    pub_control = context.socket(zmq.PUB)
    sub_ue.connect(UE_METRICS_ENDPOINT)
    sub_slice.connect(SLICE_METRICS_ENDPOINT)
    pub_control.bind(SLICE_BUDGET_ENDPOINT)
    for socket in (sub_ue, sub_slice):
        socket.setsockopt(zmq.SUBSCRIBE, b"")
        socket.setsockopt(zmq.CONFLATE, 1)
    poller = zmq.Poller()
    poller.register(sub_ue, zmq.POLLIN)
    poller.register(sub_slice, zmq.POLLIN)

    total_prbs = N_PRB
    total_prbs_locked = False
    last_tx_samples: Dict[int, Tuple[float, int, int]] = {}
    slot_axis = 0
    observation_block_idx = 0
    last_se_per_slice = {sid: float(CQI_TO_SE_NR[1]) for sid in SLICE_IDS}
    history = {sid: deque(maxlen=MAX_PRB_WINDOW) for sid in SLICE_IDS}
    deployed_allocation: Optional[np.ndarray] = None
    deployed_max_prbs: Optional[np.ndarray] = None

    try:
        while True:
            events = dict(poller.poll(POLL_TIMEOUT_MS))
            if not events:
                continue

            if sub_slice in events and events[sub_slice] == zmq.POLLIN:
                try:
                    message = slice_metrics_pb2.SliceMetrics()
                    message.ParseFromString(sub_slice.recv())
                except Exception as exc:
                    _warn(f"discarding malformed slice telemetry: {exc}")
                else:
                    if not total_prbs_locked:
                        for item in message.slices:
                            if item.slice_id == 0 and item.cfg_max_prb > 0:
                                observed_prbs = int(item.cfg_max_prb)
                                if observed_prbs != total_prbs:
                                    if controller.block_index != 0:
                                        raise RuntimeError("cell PRB capacity arrived after first update")
                                    if block.se_rows:
                                        if deployed_allocation is None:
                                            raise RuntimeError(
                                                "partial objective block has no deployed allocation"
                                            )
                                        _write_objective_block(
                                            logs.writers.get("objective_block"),
                                            observation_block_idx,
                                            controller.block_index,
                                            "aborted_capacity_change",
                                            f"cell PRB capacity changed from {total_prbs} to {observed_prbs}",
                                            block,
                                            total_prbs,
                                            deployed_allocation,
                                            deployed_allocation,
                                            None,
                                        )
                                        observation_block_idx += 1
                                        logs.flush()
                                    total_prbs = observed_prbs
                                    controller = SlicingController(total_prbs)
                                    block.reset()
                                    deployed_allocation = None
                                    deployed_max_prbs = None
                                    history = {sid: deque(maxlen=MAX_PRB_WINDOW) for sid in SLICE_IDS}
                                total_prbs_locked = True
                                poller.unregister(sub_slice)
                                sub_slice.close(0)
                                sub_slice = None
                                break

            if sub_ue not in events:
                continue
            started = time.perf_counter()
            try:
                telemetry = metrics_pb2.Metrics()
                telemetry.ParseFromString(sub_ue.recv())
            except Exception as exc:
                _warn(f"discarding malformed UE telemetry and holding allocation: {exc}")
                continue

            raw_tti = int(getattr(telemetry, "tti_cnt", 0))
            target_ues = [ue for ue in telemetry.ue_metrics if ue.slice_id in SLICE_IDS]
            is_new = block.is_new_tti(raw_tti)
            collect_sample = is_new and deployed_allocation is not None
            sample_slots = (
                _forward_tti_delta(raw_tti, block.last_tti) if is_new else 0
            )
            if is_new:
                slot_axis += sample_slots
            _write_cqi_rows(logs.writers.get("cqi"), raw_tti, telemetry.ue_metrics)

            slice_cqis: Dict[int, List[int]] = defaultdict(list)
            slice_active: Dict[int, List[bool]] = defaultdict(list)
            served_bytes: Dict[int, float] = defaultdict(float)
            for ue in target_ues:
                current = float(ue.tx_bytes)
                previous = last_tx_samples.get(ue.rnti)
                raw_delta = 0.0
                if is_new:
                    raw_delta, interval_slots, next_sample = (
                        _counter_interval_and_baseline(
                            current, raw_tti, int(ue.slice_id), previous
                        )
                    )
                    if raw_delta > 0.0 and previous is not None:
                        _write_counter_interval(
                            logs.writers.get("counter"), ue.rnti, int(ue.slice_id),
                            slot_axis - interval_slots, slot_axis,
                            previous[1], raw_tti, raw_delta,
                        )
                    if next_sample is not None:
                        last_tx_samples[ue.rnti] = next_sample
                slice_cqis[ue.slice_id].append(int(ue.cqi))
                slice_active[ue.slice_id].append(
                    raw_delta > 0.0 or ue.dl_buffer > 0
                )
                served_bytes[ue.slice_id] += raw_delta

            learning_se: Dict[int, float] = {}
            actuation_se: Dict[int, float] = {}
            for sid in SLICE_IDS:
                current_se = (
                    slice_pf_se(slice_cqis[sid], slice_active[sid])
                    if slice_cqis[sid]
                    else None
                )
                if is_new and current_se is not None:
                    last_se_per_slice[sid] = current_se
                learning_se[sid] = last_se_per_slice[sid]
                actuation_se[sid] = (
                    current_se if current_se is not None else last_se_per_slice[sid]
                )
            if is_new and deployed_allocation is None:
                block.last_tti = raw_tti
            sample_allocation = (deployed_allocation if deployed_allocation is not None else controller.current_allocation).copy()
            sample_prbs, sample_residuals = shares_to_prbs(sample_allocation, total_prbs)
            sample_max_prbs = (
                deployed_max_prbs.copy()
                if deployed_max_prbs is not None
                else sample_prbs.copy()
            )
            if collect_sample:
                block.add(raw_tti, learning_se, served_bytes, slot_axis)
                block.prb_sum += sample_prbs
                block.max_prb_sum += sample_max_prbs
                block.residual_sum += sample_residuals
                block.residual_abs_max = max(
                    block.residual_abs_max, float(np.max(np.abs(sample_residuals)))
                )
                _write_objective_sample(
                    logs.writers.get("objective_sample"), observation_block_idx,
                    controller.block_index, len(block.se_rows) - 1, raw_tti,
                    slot_axis, block.last_elapsed, block.se_rows[-1],
                )
                _write_tti_rows(
                    logs.writers.get("tti"), raw_tti, controller.block_index,
                    sample_allocation, actuation_se, block.se_rows, sample_prbs,
                    sample_max_prbs, sample_residuals, total_prbs, served_bytes,
                    block.last_elapsed, (time.perf_counter() - started) * 1e3,
                )

            block_result = None
            block_update_ms = 0.0
            completed_block = block.ready
            completed_controller_block_idx = controller.block_index
            completed_allocation = sample_allocation.copy()
            update_error = ""
            if completed_block:
                update_started = time.perf_counter()
                try:
                    block_result = controller.update_block(block.se_rows)
                except (ValueError, FloatingPointError) as exc:
                    update_error = str(exc)
                    _warn(f"block update rejected; holding last valid allocation: {exc}")
                else:
                    block_update_ms = (time.perf_counter() - update_started) * 1e3

            meta_allocation = controller.current_allocation.copy()
            min_prbs, _ = shares_to_prbs(meta_allocation, total_prbs)
            max_prbs = np.zeros(len(SLICE_IDS), dtype=int)
            budget_message = slice_budgets_pb2.SliceBudgets()
            budget_message.tti = raw_tti if raw_tti > 0 else 0
            _append_builtin_slice_budgets(budget_message, total_prbs)
            for index, sid in enumerate(SLICE_IDS):
                budget = budget_message.budgets.add()
                budget.slice_id = sid
                budget.min_prb = max(int(min_prbs[index]), 0)
                modeled = meta_allocation[index] * total_prbs * B_PRB_HZ * actuation_se[sid]
                history[sid].append((modeled, budget.min_prb))
                history_max = max(history[sid], key=lambda item: item[0])[1]
                budget.max_prb = max(budget.min_prb, history_max)
                max_prbs[index] = budget.max_prb
                budget.priority = PRIORITY[sid]
            pub_control.send(budget_message.SerializeToString())
            deployed_allocation = meta_allocation
            deployed_max_prbs = max_prbs

            if completed_block:
                _write_objective_block(
                    logs.writers.get("objective_block"), observation_block_idx,
                    completed_controller_block_idx,
                    "updated" if block_result is not None else "held_update_error",
                    update_error, block, total_prbs, completed_allocation,
                    meta_allocation, block_result,
                )
                if block_result is not None:
                    writer = logs.writers.get("block")
                    if writer is not None:
                        writer.writerow(build_block_row(
                            block_result, block, total_prbs, block_update_ms,
                            min_prbs, max_prbs,
                        ))
                    write_expert_rows(logs.writers.get("expert"), block_result)
                observation_block_idx += 1
                block.reset()
                logs.flush()
    finally:
        logs.close()
        for socket in filter(None, (sub_ue, sub_slice, pub_control)):
            socket.close(0)
if __name__ == "__main__":
    try:
        run_live()
    except KeyboardInterrupt:
        print("Stopping SLA-aware slicing controller.")
