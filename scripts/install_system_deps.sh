#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/.." && pwd -P)"

dry_run=false
with_dpdk=false
with_uhd=false
with_numa=false
with_tests=false
with_redis=false
with_gui=false
with_tools=false

usage() {
  cat <<'USAGE'
Usage: scripts/install_system_deps.sh [options]

Install the minimal native dependencies on supported apt-based systems.
The default set builds the EdgeRIC-enabled gNB with ZeroMQ and FFTW.

Options:
  --dry-run       Print apt commands without changing the system
  --with-dpdk     Add optional DPDK development files (requires >= 22.11)
  --with-uhd      Add optional UHD SDR development/runtime packages
  --with-numa     Add optional standalone NUMA support
  --with-tests    Add GoogleTest development files
  --with-redis    Add the optional Redis service used by muApp1/muApp3
  --with-gui      Add the Tk backend used by the graphical muApp3 monitor
  --with-tools    Add optional ccache, Mold, Ninja, and libdw tooling
  -h, --help      Show this help

This script does not configure hugepages, VFIO, IOMMU, NIC binding, CPU
isolation, or device permissions. Those settings are host- and device-specific.
USAGE
}

while (($#)); do
  case "$1" in
    --dry-run) dry_run=true ;;
    --with-dpdk) with_dpdk=true ;;
    --with-uhd) with_uhd=true ;;
    --with-numa) with_numa=true ;;
    --with-tests) with_tests=true ;;
    --with-redis) with_redis=true ;;
    --with-gui) with_gui=true ;;
    --with-tools) with_tools=true ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'error: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
  shift
done

[[ -f "${repo_root}/CMakeLists.txt" ]] || {
  printf 'error: repository root could not be resolved from %s\n' "$script_dir" >&2
  exit 1
}

[[ -r /etc/os-release ]] || {
  printf 'error: /etc/os-release is unavailable; this installer supports apt-based Linux only.\n' >&2
  exit 1
}

# shellcheck disable=SC1091
source /etc/os-release
os_id="${ID:-}"
os_version="${VERSION_ID:-}"
case "${os_id}:${os_version}" in
  ubuntu:24.04|debian:12|debian:13)
    ;;
  *)
    printf 'error: dependency installer not validated on %s (ID=%s, VERSION_ID=%s).\n' \
      "${PRETTY_NAME:-unknown}" "${os_id:-unknown}" "${os_version:-unknown}" >&2
    printf 'Validated targets: Ubuntu 24.04 LTS and Debian 12/13.\n' >&2
    printf 'This safety stop is not proof of incompatibility; do not bypass the allowlist blindly.\n' >&2
    printf 'See %s/README.md#compatibility-safety-gates for the validation path.\n' "$repo_root" >&2
    exit 1
    ;;
esac

command -v apt-get >/dev/null 2>&1 || {
  printf 'error: apt-get is unavailable on this host.\n' >&2
  exit 1
}

root_prefix=()
if ((EUID != 0)); then
  root_prefix=(sudo)
  if ! $dry_run && ! command -v sudo >/dev/null 2>&1; then
    printf 'error: sudo is required when this script is run as a non-root user.\n' >&2
    exit 1
  fi
fi

print_command() {
  printf '+'
  printf ' %q' "$@"
  printf '\n'
}

run_command() {
  print_command "$@"
  if ! $dry_run; then
    "$@"
  fi
}

required_packages=(
  binutils
  build-essential
  ca-certificates
  cmake
  cppzmq-dev
  git
  libfftw3-dev
  libmbedtls-dev
  libprotobuf-dev
  libsctp-dev
  libyaml-cpp-dev
  libzmq3-dev
  pkg-config
  protobuf-compiler
  python3
  python3-venv
)

optional_packages=()
$with_dpdk && optional_packages+=(libdpdk-dev)
$with_uhd && optional_packages+=(libuhd-dev uhd-host)
$with_numa && optional_packages+=(libnuma-dev)
$with_tests && optional_packages+=(libgtest-dev)
$with_redis && optional_packages+=(redis-server redis-tools)
$with_gui && optional_packages+=(python3-tk)
$with_tools && optional_packages+=(ccache libdw-dev mold ninja-build)

run_command "${root_prefix[@]}" env DEBIAN_FRONTEND=noninteractive apt-get update

if $with_dpdk && ! $dry_run; then
  dpdk_candidate="$(
    apt-cache policy libdpdk-dev |
      awk '$1 == "Candidate:" {print $2; exit}'
  )"
  if [[ -z "$dpdk_candidate" || "$dpdk_candidate" == "(none)" ]]; then
    printf 'error: this distribution has no libdpdk-dev candidate.\n' >&2
    exit 1
  fi
  if ! dpkg --compare-versions "$dpdk_candidate" ge 22.11; then
    printf 'error: libdpdk-dev candidate %s is older than required DPDK 22.11.\n' \
      "$dpdk_candidate" >&2
    printf 'No PPA or source build is configured; omit --with-dpdk or install a reviewed toolchain.\n' >&2
    exit 1
  fi
fi

all_packages=("${required_packages[@]}" "${optional_packages[@]}")
run_command \
  "${root_prefix[@]}" \
  env DEBIAN_FRONTEND=noninteractive \
  apt-get install --no-install-recommends --yes \
  "${all_packages[@]}"

if $dry_run; then
  printf 'Dry run complete; no package database or installed package was changed.\n'
else
  "${script_dir}/check_protobuf_toolchain.sh"
  printf 'Native dependency installation and Protobuf consistency check complete.\n'
fi

if $with_dpdk; then
  printf '%s\n' \
    'DPDK files were requested. Hugepages, IOMMU/VFIO, NIC binding, permissions,' \
    'and CPU isolation remain manual host-administration steps.'
fi
if $with_uhd; then
  printf '%s\n' \
    'UHD files were requested. Device firmware/images, USB permissions, and network' \
    'interface configuration remain hardware-specific.'
fi
printf 'No external ROHC library is required by this repository build.\n'
