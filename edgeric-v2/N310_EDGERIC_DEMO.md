# N310 and EdgeRIC demo

This guide shows how to run the N310 Split 8 demo with EdgeRIC and Ettus
Research's USRP N310. UHD (USRP Hardware Driver) is the software interface
between srsRAN and the radio. The demo covers the build, srsUE attachment,
downlink traffic, telemetry, and alternating budgets on the two built-in
scheduler slices.

## Deployment context

![KORA-oriented EdgeRIC deployment with alternative core, CU, DU, and RU placements](images/kora_edgeric_architecture.png)

The figure shows the complementary KORA Split 7.2 setup. Its O-DU and O-RU are
distinct logical functions connected over O-RAN Open Fronthaul, and Keysight
RuSIM provides the O-RU and UE side. This guide uses Split 8 instead: the PHY
remains in the OCUDU/srsRAN gNB, while an Ettus Research USRP N310 provides the
RF front end through UHD. The N310 is an SDR in this split, not the Split 7.2
O-RU shown above; srsUE with a USRP provides the UE side.

In either setup, the 5G core may be Keysight CoreSIM, part of a Keysight CuSIM
deployment, or another compatible implementation. The CU may be supplied by
CuSIM or OCUDU/srsRAN; the OCUDU/srsRAN CU and DU can run separately over F1 or
together in one gNB process. The EdgeRIC RT-E2 agent remains with the DU
scheduler.

## Testbed prerequisites

You need:

- an Ubuntu 24.04 LTS or Debian 12/13 gNB host with `sudo` and systemd;
- an N310 reachable from that host, with compatible UHD/FPGA versions;
- the clock, PPS, RF, and network connections required by your deployment;
- a reachable 5G core whose PLMN, TAC, slices, and data-network routing match
  the gNB and subscriber configuration;
- a separately installed and configured srsUE host and radio; and
- an `iperf3` client on the UE host for the throughput demonstration.

This repository provisions only the gNB host. Configure the core, UE host,
N310 networking, timing, and RF separately.

## 1. Clone, install, and build

On the gNB host:

```bash
git clone https://github.com/azulqarni/srsran-er-kora.git
cd srsran-er-kora

./scripts/install_system_deps.sh --with-uhd
./scripts/setup_edgeric_venv.sh
./build-srs-gnb-er.sh --clean --uhd
```

The captured run used commit `f6aae3e`; the commands below retain that run's
output as provenance while using the current `main` branch for setup.

`install_system_deps.sh` installs native build and UHD packages system-wide;
they remain after the clone is deleted. `setup_edgeric_venv.sh` creates the
repository-local `.venv` used by the muApps below. The optional inherited
muApp environment and Redis are not needed for this demo.

A successful build ends similarly to:

```text
[100%] Linking CXX executable gnb
[100%] Built target gnb
gNB binary: .../srsran-er-kora/build/apps/gnb/gnb
CMake: cmake version ...
Compiler: ...
Linker: ...
Protobuf: ...
```

Optional UHD checks before launching the gNB:

```bash
uhd_config_info --version
ping -c 3 <N310_IP>
uhd_find_devices --args="type=n3xx,addr=<N310_IP>"
uhd_usrp_probe --args="type=n3xx,addr=<N310_IP>"
```

## 2. Prepare the site configuration

Keep the deployment-specific configuration outside the public repository.
The tracked generic 20 MHz profile can be copied as a sanitized starting
point:

```bash
cp configs/gnb_rf_n310_fdd_n3_20mhz.yml /path/to/site-specific-n310.yml
```

It will not reproduce the captured 10 MHz/52-PRB output unchanged. Review the
AMF and bind addresses, N310 address and device arguments, clock and time
sources, gains, band, channel bandwidth, PLMN, TAC, and slice settings. The
captured run used a deployment-owned 10 MHz band-3 profile.

## 3. Start the gNB

In gNB terminal 1, from the repository root:

```bash
./start_fns_gnb.sh /path/to/site-specific-n310.yml
```

The launcher uses `build/apps/gnb/gnb` and starts a transient systemd service.
Representative success output is:

```text
Starting gNB as root with IPC group <GROUP> (umask 0007).
Running as unit: gnb.service

--== srsRAN gNB (commit f6aae3e) ==--

Available radio types: uhd and zmq.
[INFO] [UHD] ...
Cell pci=1, bw=10 MHz, 1T1R, ... (n3) ...
N2: Connection to AMF on <AMF_IP>:38412 completed
==== gNB started ===
```

The essential checks are successful N310 initialization, the expected cell
parameters, an N2 connection, and `==== gNB started ====`.

## 4. Attach the UE

On UE terminal 1, launch the separately built and configured srsUE. For the
captured setup:

```bash
./srsRAN_4G/build/srsue/src/srsue \
  srsRAN_4G/srsue/ue_rf.conf \
  --log.phy_level=debug \
  --log.mac_level=debug \
  --log.rrc_level=debug \
  --log.rf_level=debug
```

The UE checkout, radio configuration, subscriber credentials, and device
setup are not supplied by this repository. Successful attachment looks like:

```text
Random Access Complete.     c-rnti=0x4601, ta=0
RRC Connected
PDU Session Establishment successful. IP: <UE_TUNNEL_IP>
RRC NR reconfiguration successful.
```

Use the address printed here in the traffic command; it can change between
sessions.

## 5. Start telemetry and control

After attachment, the demonstration uses six terminals:

| Host | Terminal | Process |
| --- | --- | --- |
| gNB | 1 | gNB |
| gNB | 2 | Per-UE bitrate monitor |
| gNB | 3 | Slice telemetry monitor |
| gNB | 4 | Slice-budget controller |
| UE | 1 | srsUE |
| UE | 2 | Reverse-mode `iperf3` client |

Activate the base environment in each gNB muApp terminal:

```bash
cd ~/srsran-er-kora
source .venv/bin/activate
```

In gNB terminal 2, start the per-UE bitrate monitor:

```bash
python edgeric-v2/muApp3/muApp3_bitrate_monitor.py
```

In gNB terminal 3, start the slice monitor:

```bash
python edgeric-v2/muApp4/slice_metrics.py
```

The included flipper targets IDs 2 and 3 on a 273-PRB cell and produces
270/2 bounds. For this 10 MHz check, set these five assignments in
`edgeric-v2/muApp4/slice_budgets_flipper.py`:

```python
SLICE_IDS = [0, 1]
PRB_TOTAL = 52
HEAVY_SHARE = 0.0
FLIP_PERIOD_S = 10.0
PUBLISH_TICK_S = 0.05
```

Then start gNB terminal 4:

```bash
python edgeric-v2/muApp4/slice_budgets_flipper.py
```

The controller has no periodic output; verify it in the slice monitor. Each
phase caps the SRB/signaling slice at one PRB, so run it only after attachment
on an isolated testbed.

## 6. Generate sustained downlink traffic

An optional bounded reachability check is:

```bash
ping -c 5 -I tun_srsue <DATA_NETWORK_TARGET>
```

For the throughput test, run reverse-mode TCP on UE terminal 2:

```bash
iperf3 -4 -c <IPERF3_SERVER> -p <AVAILABLE_PORT> -R \
  -B <UE_TUNNEL_IP>%tun_srsue \
  -t 80 -i 1 -P 1
```

`<UE_TUNNEL_IP>` must match the PDU-session address printed by srsUE during
attachment.

`-R` sends traffic toward the UE, `-B` binds it to the PDU-session address and
`tun_srsue`, and `-i 1` reports one-second application goodput. No UE routing
changes are required.

The capture used `ping.online.net`. Public test ports and performance vary;
use a controlled server for repeatable measurements.

Selected, non-contiguous output from the capture:

```text
Reverse mode, remote host <IPERF3_SERVER> is sending
[  5]  10.00-11.00 sec  3.38 MBytes  28.3 Mbits/sec
[  5]  17.00-18.00 sec  0.00 Bytes  0.00 bits/sec
...
[  5]  26.00-27.00 sec  3.38 MBytes  28.3 Mbits/sec
[  5]  37.00-38.00 sec  0.00 Bytes  0.00 bits/sec
...
[  5]  46.00-47.00 sec  3.25 MBytes  27.3 Mbits/sec
```

The repeated high (about 27-28 Mbps) and low (about 0-1 Mbps) windows align
with the slice telemetry below. TCP transitions account for intermediate
samples.

## 7. Inspect the correlated telemetry

The per-UE monitor showed the same repeated high/low pattern in five-second
radio-counter windows:

```text
Bitrate report (Dt=5.0s)
UE 17921 (slice 1): TX=28.983 Mbps  RX=0.682 Mbps
Slice 1: TX=28.983 Mbps  RX=0.682 Mbps

Bitrate report (Dt=5.0s)
UE 17921 (slice 1): TX=0.631 Mbps  RX=0.137 Mbps
Slice 1: TX=0.631 Mbps  RX=0.137 Mbps
```

This pattern repeated across the capture. TX is downlink; RX is uplink.

The slice monitor independently captured both control phases:

```text
TTI 4244
  slice=0 min=52 max=52 prio=128 dl_rbs=0 ul_rbs=0 avg_dl=0.0 avg_ul=0.0 hol_slots=0
  slice=1 min=1 max=1 prio=128 dl_rbs=1 ul_rbs=0 avg_dl=1.0 avg_ul=0.0 hol_slots=0

TTI 4067
  slice=0 min=1 max=1 prio=128 dl_rbs=0 ul_rbs=0 avg_dl=0.0 avg_ul=0.0 hol_slots=0
  slice=1 min=52 max=52 prio=128 dl_rbs=48 ul_rbs=0 avg_dl=45.4 avg_ul=1.3 hol_slots=0
```

The printed fields mean:

| Field | Meaning |
| --- | --- |
| `TTI` | Radio-slot index; it wraps at the system-frame cycle. |
| `slice` | Internal scheduler slice ID. |
| `min`, `max` | Current per-slot PRB target and cap. They are absolute PRB counts, not percentages; `min` raises scheduling preference but is not a reservation. |
| `prio` | Configured slice priority from 0 to 255; larger values receive more preference within the scheduler's other rules. |
| `dl_rbs` | PDSCH RBs allocated to the slice in the reported slot. |
| `ul_rbs` | PUSCH RBs recorded across the scheduler's pending UL look-ahead slots, not strictly the reported slot. |
| `avg_dl`, `avg_ul` | Exponentially smoothed PDSCH/PUSCH RBs per slot; the newest slot has weight 0.1. |
| `hol_slots` | Largest downlink head-of-line packet age across the slice's logical channels, in slots. |


The TTI counter wrapped between these samples. `min` and `max` show the
applied bounds, while the active data slice used one versus 48 DL PRBs.
Bounds limit allocation but do not guarantee consumption.

## 8. Slice-control behavior

The two monitors report telemetry. The flipper publishes scheduler bounds on
`/tmp/control_slice_budgets`, reversing them every ten seconds.

The gNB always creates two built-in scheduler slices: internal ID 0 carries
SRB/signaling traffic and ID 1 is the default DRB/data slice. Configured
RRM-policy slices begin at ID 2 in configuration order. These internal IDs
are not SST or SD values.

With the adjusted constants above, the flipper publishes fixed
`min_prb == max_prb` bounds of slice 0 = 1 and slice 1 = 52 at startup, then
reverses them to 52/1 and continues alternating every ten seconds while
republishing every 50 ms. The one-PRB floor makes the two limits total 53 on
a 52-PRB cell. They are independent per-slice bounds, not a feasible
partition or a promise that all configured PRBs will be consumed.

Only slice 1 carried UE data, so this run demonstrates throttling of the
default data slice rather than contention between data slices. That test
requires two configured data slices, normally IDs 2 and 3, matching core and
UE S-NSSAIs, with traffic on both.

Only one controller may bind `/tmp/control_slice_budgets`. The gNB retains
the last received policy after the controller exits, so restart the gNB after
a control experiment to restore the configuration baseline.

## 9. Shutdown

Stop `iperf3`, the flipper, both telemetry monitors, and the UE with `Ctrl-C`.
Stop the transient gNB service authoritatively from another terminal and verify
that it is inactive:

```bash
sudo systemctl stop gnb.service
systemctl is-active gnb.service  # expect: inactive
```

`Ctrl-C` in the attached gNB terminal may stop the process or may only detach
from the transient service; always verify its state with `systemctl is-active`.
Press `Ctrl-]` three times to detach without stopping the gNB.
