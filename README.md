# srsRAN Project with EdgeRIC RT-E2 fork

This repository uses [srsRAN Project](https://github.com/srsran/srsRAN_Project)
25.04.0 as its base. It adds a local RT-E2 telemetry and control interface for
EdgeRIC while using srsRAN as the underlying RAN codebase and retaining its
reference documentation.

The EdgeRIC integration adapts the per-UE telemetry and control work from
[EdgeRIC-on-5G](https://github.com/ucsdwcsng/EdgeRIC-on-5G) by UCSD WCSNG and
extends it with inter-slice telemetry and control. The workflow below is
authoritative for this fork. The inherited material remains under
[Upstream srsRAN Project reference](#upstream-srsran-project-reference).

## Deployment options and testbeds

![KORA-oriented EdgeRIC deployment with alternative core, CU, DU, and RU placements](edgeric-v2/images/kora_edgeric_architecture.png)

The diagram shows a KORA-oriented deployment; the component boundaries can be
combined differently. Depending on the lab setup, the 5G core may be provided
by Keysight CoreSIM, as part of a Keysight CuSIM deployment, or by another
compatible implementation. The CU may be supplied by CuSIM or OCUDU/srsRAN.
The OCUDU/srsRAN CU and DU may run as separate logical functions over F1 or
together in one gNB process. The local RT-E2 agent connects the DU scheduler
to EdgeRIC muApps over ZeroMQ and Protobuf.

This repository is used with two complementary radio setups:

- **Keysight Open RAN Architect (KORA), Split 7.2:** the O-DU and O-RU are
  distinct logical functions, separated at an intra-PHY boundary and joined by
  O-RAN Open Fronthaul. Keysight RuSIM provides the O-RU and UE side.
- **UvA/SNE 5G/6G lab, Split 8:** the PHY remains with the OCUDU/srsRAN gNB,
  while an Ettus Research USRP N310 provides the RF front end through UHD.
  The N310 is an SDR in this split, not the Split 7.2 O-RU shown above; srsUE
  with a USRP B210 provides the UE side.

## Setup

Supported package-manager targets are Ubuntu 24.04 LTS and Debian 12/13. Run
the following from the repository root for the bundled Split-7.2/DPDK
configuration:

1. Install the native build dependencies, including optional DPDK support.

   ```bash
   ./scripts/install_system_deps.sh --with-dpdk
   ```

2. Create the isolated `.venv`, install the base EdgeRIC Python packages,
   generate Python Protobuf bindings, and run the import smoke test.

   ```bash
   ./scripts/setup_edgeric_venv.sh
   ```

3. Generate C++ Protobuf bindings and build the DPDK-enabled gNB.

   ```bash
   ./build-srs-gnb-er.sh --clean --dpdk
   ```

The binary is written to `build/apps/gnb/gnb`. The bundled configuration is
DPDK-specific. To build for a different, non-DPDK radio configuration, omit
`--with-dpdk` and `--dpdk`. Use
`./scripts/install_system_deps.sh --dry-run` to inspect the apt commands first.

The base Python environment supports CPython 3.10–3.13, including Debian 13's
default CPython 3.13. Activate it with:

```bash
source .venv/bin/activate
```

For the optional legacy RL/plotting muApps, use CPython 3.10 or 3.11 and a
separate environment:

```bash
./scripts/setup_edgeric_venv.sh --venv .venv-muapps --python python3.11 --with-muapps
source .venv-muapps/bin/activate
```

Debian 13's default CPython 3.13 supports the base environment only. The
installer does not add third-party Python repositories; provide a separately
managed CPython 3.10 or 3.11 interpreter if the legacy muApp stack is needed.

The dependency installer does not configure hugepages, IOMMU/VFIO, NIC
binding, CPU isolation, or device permissions. Configure those for the target
host before using DPDK. This repository does not require an external ROHC
installation. Run each script with `--help` for optional UHD, NUMA, Redis, GUI,
test, and build-tool support.

### Compatibility safety gates

An OS or base-Python “not validated” error marks an untested combination, not
a demonstrated incompatibility. Do not delete or broadly widen the exact
allowlists. Record the target details first:

```bash
cat /etc/os-release
uname -m
python3 -VV
```

For a new OS release, verify the required and selected optional package names,
then add only its exact `ID:VERSION_ID` after review and run the installer dry
run plus a clean native build. For a new Python minor, use `--python PATH` with
an installed supported CPython, or validate the pinned wheels, generated
Protobuf bindings, and smoke test before extending the exact version case. The
`--with-muapps` limit is different: it reflects known legacy API and dependency
incompatibilities rather than an untested version.

## Running

The default `configs/fns_4x4_100mhz_slicing.yaml` is a site-specific example.
Before running it, review the AMF and bind addresses, DPDK lcores and PCI device,
RU/DU MAC addresses, VLANs, cell parameters, and slice definitions.

Start the gNB with the bundled configuration:

```bash
./start_fns_gnb.sh
```

Or pass another configuration explicitly:

```bash
./start_fns_gnb.sh /path/to/gnb-config.yaml
```

### N310 profile

For a terminal-by-terminal walkthrough with representative output, see the
[N310 and EdgeRIC demo](edgeric-v2/N310_EDGERIC_DEMO.md).

The tracked `configs/gnb_rf_n310_fdd_n3_20mhz.yml` profile is a sanitized
starting point, not a deployment-ready configuration. Keep site-specific
profiles outside the public repository and copy the generic profile before
editing it:

```bash
cp configs/gnb_rf_n310_fdd_n3_20mhz.yml /path/to/site-specific-n310.yml
```

Review the AMF and bind addresses, N310 address and device arguments, clock and
time sources, gains, band, channel bandwidth, PLMN, TAC, and slice settings.
After completing the common Python environment setup above, install UHD
support, build the UHD-enabled gNB, and start it with the deployment-owned
profile:

```bash
./scripts/install_system_deps.sh --with-uhd
./build-srs-gnb-er.sh --clean --uhd
./start_fns_gnb.sh /path/to/site-specific-n310.yml
```

The launcher uses `build/apps/gnb/gnb`, starts the gNB as a transient root
service, and assigns its IPC files to the invoking user's primary group with a
group-writable umask. EdgeRIC and muApp Python processes can therefore run as
the normal user—do not run them with `sudo`. Set `GNB_GROUP` to another group
only when the invoking user is already a member of it.

In another terminal, activate the base environment and monitor per-UE bitrate
and telemetry:

```bash
source .venv/bin/activate
python edgeric-v2/muApp3/muApp3_bitrate_monitor.py
```

To monitor the inter-slice telemetry stream instead, run:

```bash
python edgeric-v2/muApp4/slice_metrics.py
```

Press `Ctrl-C` in the attached gNB terminal to stop it. If necessary, stop the
transient service from another terminal with `sudo systemctl stop gnb.service`.
See [edgeric-v2/README.md](edgeric-v2/README.md) for control examples and the
full legacy muApps; review their policy values before running control scripts.

## RT-E2 telemetry and control (EdgeRIC integration)

Here, **RT-E2** means the local ZeroMQ PUB/SUB IPC interface between the gNB and
EdgeRIC/muApps; it is separate from the native O-RAN E2AP subsystem.

- Per-UE telemetry (`ipc:///tmp/metrics`, message `Metrics`) is published after
  UE scheduling at the end of each TTI. `Metrics` carries `tti_cnt`; each
  repeated `UeMetrics` carries `rnti`, `cqi`, `snr`, `tx_bytes`, `rx_bytes`,
  `dl_buffer`, `ul_buffer`, `dl_tbs`, and `slice_id`.
- Per-slice telemetry (`ipc:///tmp/slice_metrics`, message `SliceMetrics`) is
  published each TTI after intra-slice scheduling. `SliceMetrics` carries `tti`;
  each slice carries `slice_id`, current `cfg_min_prb`, `cfg_max_prb`,
  `cfg_priority`, `dl_rbs_used`, `ul_rbs_used`, `avg_dl_rbs_per_slot`,
  `avg_ul_rbs_per_slot`, and `hol_slots` (maximum DL head-of-line age in slots).
- Per-UE control (`ipc:///tmp/control_weights_actions` and
  `ipc:///tmp/control_mcs_actions`) uses non-blocking subscribers to read
  scheduling weights and MCS overrides keyed by RNTI before scheduling.
- Per-slice control (`ipc:///tmp/control_slice_budgets`) uses a non-blocking
  subscriber to read `SliceBudgets` (`min_prb`, `max_prb`, and `priority` per
  slice) at the start of inter-slice `slot_indication`.

The five hand-authored schemas in `lib/protobufs/` are the source of truth.
CMake generates disposable C++ bindings in `build/lib/edgeric/`; the Python
setup generates disposable `*_pb2.py` files in `edgeric-v2/protobufs/`.
Historical schemas in `edgeric-v2/protobufs/original/` are archives, not
generation inputs. Generated bindings are intentionally ignored because their
generator and runtime must be selected together on each host.

### Checks and cleanup

| Task | Command |
|---|---|
| Diagnose native and Python Protobuf versions | `./scripts/check_protobuf_toolchain.sh --python .venv/bin/python` |
| Regenerate Python bindings and smoke-test | `./scripts/setup_edgeric_venv.sh --generate-only` |
| Repeat only the Python import smoke test | `./scripts/setup_edgeric_venv.sh --smoke-only` |
| Rebuild cleanly with DPDK | `./build-srs-gnb-er.sh --clean --dpdk` |
| Remove the verified build directory | `./build-srs-gnb-er.sh --clean-only` |
| Remove the verified base venv | `./scripts/setup_edgeric_venv.sh --clean` |

Native gNB builds validate only the native Protobuf toolchain. Python runtime
compatibility is checked explicitly by the Python setup and generation
commands.

---

## Upstream srsRAN Project reference

The following material is inherited from srsRAN Project and is retained for
background. It describes generic upstream installation and deployment, not the
fresh-clone workflow for this EdgeRIC fork.

<details>
<summary><strong>Show inherited upstream project information</strong></summary>

[![Build Status](https://github.com/srsran/srsRAN_Project/actions/workflows/ccpp.yml/badge.svg?branch=main)](https://github.com/srsran/srsRAN_Project/actions/workflows/ccpp.yml)
[![OpenSSF Best Practices](https://www.bestpractices.dev/projects/7868/badge)](https://www.bestpractices.dev/projects/7868)

The srsRAN Project is a complete 5G RAN solution, featuring an ORAN-native CU/DU developed by [SRS](http://www.srs.io).

The solution includes a complete L1/2/3 implementation with minimal external dependencies. Portable across processor architectures, the software has been optimized for x86 and ARM. srsRAN follows the 3GPP 5G system architecture implementing the functional splits between distributed unit (DU) and centralized unit (CU). The CU is further disaggregated into control plane (CU-CP) and user-plane (CU-UP).

See the [srsRAN Project](https://www.srsran.com/) for information, guides and project news.

Build instructions and user guides - [srsRAN Project documentation](https://docs.srsran.com/projects/project).

Community announcements and support - [Discussion board](https://www.github.com/srsran/srsran_project/discussions).

Features and roadmap - [Features](https://docs.srsran.com/projects/project/en/latest/general/source/2_features_and_roadmap.html).

### Dependencies

* Build tools:
  * cmake:               <https://cmake.org/>
* Mandatory requirements:
  * libfftw:             <https://www.fftw.org/>
  * libsctp:             <https://github.com/sctp/lksctp-tools>
  * yaml-cpp:            <https://github.com/jbeder/yaml-cpp>
  * mbedTLS:             <https://www.trustedfirmware.org/projects/mbed-tls/>
* Optional requirements:
  * googletest:          <https://github.com/google/googletest/>
    * You can enable test building by using the cmake option `-DBUILD_TESTING=On`. GoogleTest is only mandatory when building with tests.

You can install the build tools and mandatory requirements for some example distributions with the commands below:

<details open>
<summary><strong>Ubuntu 22.04</strong></summary>

```bash
sudo apt-get install cmake make gcc g++ pkg-config libfftw3-dev libmbedtls-dev libsctp-dev libyaml-cpp-dev
```

</details>
<details>
<summary><strong>Fedora</strong></summary>

```bash
sudo yum install cmake make gcc gcc-c++ fftw-devel lksctp-tools-devel yaml-cpp-devel mbedtls-devel
```

</details>
<details>
<summary><strong>Arch Linux</strong></summary>

```bash
sudo pacman -S cmake make base-devel fftw mbedtls yaml-cpp lksctp-tools
```

</details>

#### Split-8

For Split-8 configurations, either UHD or ZMQ is required for the fronthaul interface. Both drivers are linked below, please see their respective documentation for installation instructions.

* UHD:                 <https://github.com/EttusResearch/uhd>
* ZMQ:                 <https://zeromq.org/>

#### Split-7.2

For Split-7.2 configurations no extra 3rd-party dependencies are required, only those listed above.

Optionally, DPDK can be installed for high-bandwidth low-latency scenarios. For more information on this, please see [this tutorial](https://docs.srsran.com/projects/project/en/latest/tutorials/source/dpdk/source/index.html#).

### Build instructions

Download and build srsRAN:

<details open>
<summary><strong>Vanilla Installation</strong></summary>

First, clone the srsRAN Project repository:

```bash
    git clone https://github.com/srsRAN/srsRAN_Project.git
```

Then build the code-base:

```bash
    cd srsRAN_Project
    mkdir build
    cd build
    cmake ../
    make -j $(nproc)
```

You can now run the gNB from ``srsRAN_Project/build/apps/gnb/``. If you wish to install the srsRAN Project gNB, you can use the following command:

```bash
    sudo make install
```

</details>

<details>
<summary><strong>ZMQ Enabled Installation</strong></summary>

Once ZMQ has been installed you will need build of srsRAN Project with the correct flags to enable the use of ZMQ.

The following commands can be used to clone and build srsRAN Project from source. The relevant flags are added to the ``cmake`` command to enable the use of ZMQ:

```bash
git clone https://github.com/srsran/srsRAN_Project.git
cd srsRAN_Project
mkdir build
cd build
cmake ../ -DENABLE_EXPORT=ON -DENABLE_ZEROMQ=ON
make -j $(nproc)
```

Pay extra attention to the cmake console output. Make sure you read the following line to ensure ZMQ has been correctly detected by srsRAN:

```bash
...
-- FINDING ZEROMQ.
-- Checking for module 'ZeroMQ'
--   No package 'ZeroMQ' found
-- Found libZEROMQ: /usr/local/include, /usr/local/lib/libzmq.so
...
```

</details>

<details>
<summary><strong>DPDK Enabled Installation</strong></summary>

Once DPDK has been installed and configured you will need to create a clean build of srsRAN Project to enable the use of DPDK.

If you have not done so already, download the code-base with the following command:

```bash
git clone https://github.com/srsRAN/srsRAN_Project.git
```

Then build the code-base, making sure to include the correct flags when running cmake:

```bash
cd srsRAN_Project
mkdir build
cd build
cmake ../ -DENABLE_DPDK=True -DASSERT_LEVEL=MINIMAL
make -j $(nproc)
```

</details>

### PHY Tests

PHY layer tests use binary test vectors and are not built by default. To enable, see the [docs](https://docs.srsran.com/projects/project/en/latest/user_manuals/source/installation.html).

### Deploying srsRAN Project

srsRAN Project can be run in two ways:

* As a monolithic gNB (combined CU & DU)
* With a split CU and DU

For exact details on running srsRAN Project in any configuration, see [the documentation](https://docs.srsran.com/projects/project/en/latest/user_manuals/source/running.html).

For information on configuring and running srsRAN for various different use cases,  check our [tutorials](https://docs.srsran.com/projects/project/en/latest/tutorials/source/index.html).


</details>
