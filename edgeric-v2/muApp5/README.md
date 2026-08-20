# muApp5

This directory contains this fork's live RT-E2 slicing controller. Complete the
[repository setup](../../README.md#setup) before running it.

## Code map

- [`oco_cqi_slicing_ktti_churn.py`](oco_cqi_slicing_ktti_churn.py)
  - the assignments at the top are the deployment configuration;
  - `block_utility_and_gradient`, `expert_step`, and `SlicingController` contain
    the controller math and state transitions;
  - `TelemetryBlock` and `CsvLogs` collect telemetry and write run artifacts;
  - `run_live` owns the ZeroMQ/protobuf runtime and publishes slice budgets.
- [`plot_kpis_ktti_churn.py`](plot_kpis_ktti_churn.py) discovers compatible
  logs, reconstructs TX-byte intervals, and writes the PRB/SLA KPI PDF.
- [`metrics.proto`](../../lib/protobufs/metrics.proto),
  [`slice_metrics.proto`](../../lib/protobufs/slice_metrics.proto), and
  [`slice_budgets.proto`](../../lib/protobufs/slice_budgets.proto) are the
  authoritative wire schemas. Generated Python bindings are described in
  [the protobuf README](../protobufs/README.md).

The controller has no command-line configuration. Review the configuration
assignments at the top of `oco_cqi_slicing_ktti_churn.py` before each
deployment.

## Runtime contract

Only slices 2–6 participate in the learned share vector. Every command also
sets built-in slice 0 to `min_prb = 0` and `max_prb =` the current cell PRB
capacity, and sets built-in slice 1 to `min_prb = max_prb = 0`. Map data DRBs to
slices 2–6; the default unconfigured slice (slice 1) remains disabled.

The controller publishes its initial allocation before collecting a complete
learning block. Each block contains `K` accepted, distinct, positive metric TTI
reports. ZeroMQ conflation means these are not necessarily `K` consecutive
physical slots. Duplicate and stale reports do not advance the block. An absent
controlled slice uses its last observed spectral efficiency, initialized from
CQI 1.

The learned share is rounded into the published minimum-PRB bound. The
maximum-PRB bound is a separate rolling-history heuristic and is never lower
than the current minimum.

Runtime IPC endpoints are:

- UE telemetry input: `ipc:///tmp/metrics`
- slice/cell telemetry input: `ipc:///tmp/slice_metrics`
- slice-budget output: `ipc:///tmp/control_slice_budgets`

Run only one process that binds the slice-budget output endpoint.

## Run and stop

From the repository root, create the base environment if needed, activate it,
and start the controller as an unprivileged user:

```bash
./scripts/setup_edgeric_venv.sh
source .venv/bin/activate
python edgeric-v2/muApp5/oco_cqi_slicing_ktti_churn.py
```

Press `Ctrl-C` to stop the muApp. This stops only the Python process; it does not
stop the gNB or clear the gNB's last cached slice budget. Restart the gNB before
a baseline run or another clean controller run.

## Plot

After at least one `K`-sample block completes, plot the latest compatible run:

```bash
source .venv/bin/activate
python edgeric-v2/muApp5/plot_kpis_ktti_churn.py --out-prefix /tmp/muapp5/kpi
```

This writes `/tmp/muapp5/kpi.pdf`. Use `python
edgeric-v2/muApp5/plot_kpis_ktti_churn.py --help` to select a run, skip initial
blocks, or set another output path.

The top panel shows published minimum-PRB bounds as integer PRB counts; it does
not show maximum bounds or actual scheduler PRB use. The bottom panel shows the
scheduler TX-byte rate divided by the configured SLA, where `1.0` meets the
target. This is not ACK-only or application-layer goodput.

The plotter requires a completed block CSV and its matching counter-interval
CSV. A header-only block CSV means that no learning block completed. The
plotter's slot duration defaults to `0.0005` seconds and is not read from the run
manifest; pass `--slot-duration-s` when the deployment uses another value.

## Logs

Each run creates five matched CSV files and one JSON manifest under
`edgeric-v2/muApp5/logs/`:

- block and counter-interval CSVs drive the current KPI plot;
- objective-sample and objective-block CSVs plus the manifest preserve the
  modeled objective inputs for future offline regret analysis;
- the expert CSV records per-expert diagnostics.

Keep artifacts with the same run ID together. Objective-sample rows are written
at the accepted-telemetry rate and can grow quickly during long runs.

For modeled-objective regret, join objective samples and blocks by
`observation_block_idx` and read the objective parameters and feasible set from
the matching manifest. Use only objective-block rows with `objective_valid = 1`
and `update_status` equal to `updated` or `held_update_error`. Ignore a trailing
sample group with no objective-block row and any `aborted_capacity_change`
group. These artifacts do not provide counterfactual realized-radio regret.
