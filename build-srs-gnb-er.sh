#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$script_dir"

build_dir_request="build"
build_type="Release"
jobs=""
clean=false
clean_only=false
configure_only=false
enable_dpdk=false
enable_uhd=false
enable_numa=false
use_mold=false
use_native_arch=false

usage() {
  cat <<'USAGE'
Usage: ./build-srs-gnb-er.sh [options]

Configure and build the EdgeRIC-enabled gNB from this repository.

Options:
  --build-dir PATH    Build directory inside the repository (default: build)
  --build-type TYPE   CMake build type (default: Release)
  --jobs N            Parallel build jobs (default: detected CPU count)
  --clean             Safely remove the verified build directory first
  --clean-only        Safely remove the verified build directory and exit
  --configure-only    Configure only; Protobuf generation runs during a build
  --dpdk              Enable optional DPDK support (requires DPDK >= 22.11)
  --uhd               Enable optional UHD radio support
  --numa              Enable optional libnuma support
  --mold              Use the optional Mold linker; fail if it is unavailable
  --native            Optimize for this host instead of a portable architecture
  -h, --help          Show this help

The default build uses ZeroMQ and FFTW, disables optional hardware backends,
tests, plugins, MKL, and ARMPL, and uses the standard system linker.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --build-dir)
      (($# >= 2)) || die "--build-dir requires a value"
      build_dir_request="$2"
      shift 2
      ;;
    --build-type)
      (($# >= 2)) || die "--build-type requires a value"
      build_type="$2"
      shift 2
      ;;
    --jobs)
      (($# >= 2)) || die "--jobs requires a value"
      jobs="$2"
      shift 2
      ;;
    --clean)
      clean=true
      shift
      ;;
    --clean-only)
      clean=true
      clean_only=true
      shift
      ;;
    --configure-only)
      configure_only=true
      shift
      ;;
    --dpdk)
      enable_dpdk=true
      shift
      ;;
    --uhd)
      enable_uhd=true
      shift
      ;;
    --numa)
      enable_numa=true
      shift
      ;;
    --mold)
      use_mold=true
      shift
      ;;
    --native)
      use_native_arch=true
      shift
      ;;
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
done

case "$build_type" in
  Debug|Release|RelWithDebInfo)
    ;;
  *)
    die "unsupported --build-type '$build_type'"
    ;;
esac

if [[ -z "$jobs" ]]; then
  jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || true)"
  jobs="${jobs:-1}"
fi
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || die "--jobs must be a positive integer"

for early_tool in cmake git mktemp realpath; do
  command -v -- "$early_tool" >/dev/null 2>&1 || {
    printf 'error: required tool not found: %s\n' "$early_tool" >&2
    printf 'Run %s/scripts/install_system_deps.sh first.\n' "$repo_root" >&2
    exit 1
  }
done

if [[ "$build_dir_request" = /* ]]; then
  build_dir="$(realpath -m -- "$build_dir_request")"
else
  build_dir="$(realpath -m -- "${repo_root}/${build_dir_request}")"
fi
case "$build_dir" in
  "${repo_root}/"*)
    ;;
  *)
    die "build directory must be inside this repository: $build_dir"
    ;;
esac
[[ "$build_dir" != "$repo_root" ]] ||
  die "refusing to use the repository root as the build directory"

marker_file="${build_dir}/.srsran-er-build-dir"

git -C "$repo_root" check-ignore --quiet --no-index -- "$marker_file" ||
  die "build directory is not covered by this repository's ignore rules: $build_dir
Use build/, build-NAME/, cmake-build-NAME/, or another explicitly ignored path."

verify_existing_build_dir() {
  [[ -d "$build_dir" ]] || return 0
  [[ ! -L "$build_dir" ]] || die "refusing symlink build directory: $build_dir"

  if [[ -f "$marker_file" ]]; then
    marker_value="$(sed -n '1p' "$marker_file")"
    [[ "$marker_value" == "$repo_root" ]] ||
      die "build marker belongs to a different source tree: $build_dir"
    [[ -f "${build_dir}/CMakeCache.txt" ]] ||
      die "build marker exists without a CMake cache: $build_dir"
  fi

  if [[ -f "${build_dir}/CMakeCache.txt" ]]; then
    cache_home="$(sed -n 's/^CMAKE_HOME_DIRECTORY:INTERNAL=//p' "${build_dir}/CMakeCache.txt")"
    [[ -n "$cache_home" ]] || die "cannot identify CMake build tree: $build_dir"
    cache_home="$(realpath -m -- "$cache_home")"
    [[ "$cache_home" == "$repo_root" ]] ||
      die "CMake build directory belongs to $cache_home, not this repository"
    return 0
  fi

  if [[ -n "$(find "$build_dir" -mindepth 1 -maxdepth 1 -print -quit)" ]]; then
    die "refusing non-empty unverified build directory: $build_dir"
  fi
}

verify_existing_build_dir
if $clean_only; then
  if [[ -d "$build_dir" ]]; then
    printf 'Removing verified build directory: %s\n' "$build_dir"
    cmake -E remove_directory "$build_dir"
  fi
  printf 'Build directory is clean: %s\n' "$build_dir"
  exit 0
fi

printf 'Preflight: checking required build dependencies.\n'
missing_tools=()
for tool in cc c++ ld make pkg-config protoc; do
  command -v -- "$tool" >/dev/null 2>&1 || missing_tools+=("$tool")
done
if (("${#missing_tools[@]}")); then
  printf 'error: missing required build tools: %s\n' "${missing_tools[*]}" >&2
  printf 'Run %s/scripts/install_system_deps.sh first.\n' "$repo_root" >&2
  exit 1
fi

declare -A header_packages=(
  [fftw3.h]=libfftw3-dev
  [google/protobuf/message.h]=libprotobuf-dev
  [mbedtls/md.h]=libmbedtls-dev
  [netinet/sctp.h]=libsctp-dev
  [yaml-cpp/yaml.h]=libyaml-cpp-dev
  [zmq.h]=libzmq3-dev
  [zmq.hpp]=cppzmq-dev
)
missing_headers=()
for header in "${!header_packages[@]}"; do
  if ! printf '#include <%s>\n' "$header" |
      c++ -E -x c++ - >/dev/null 2>&1; then
    missing_headers+=("$header (${header_packages[$header]})")
  fi
done
if (("${#missing_headers[@]}")); then
  printf 'error: missing required native headers:\n' >&2
  printf '  %s\n' "${missing_headers[@]}" >&2
  printf 'Run %s/scripts/install_system_deps.sh first.\n' "$repo_root" >&2
  exit 1
fi

declare -A pkg_modules=(
  [fftw3f]=libfftw3-dev
  [libzmq]=libzmq3-dev
  [mbedtls]=libmbedtls-dev
  [protobuf]=libprotobuf-dev
  [yaml-cpp]=libyaml-cpp-dev
)
missing_modules=()
for module in "${!pkg_modules[@]}"; do
  pkg-config --exists "$module" ||
    missing_modules+=("$module (${pkg_modules[$module]})")
done
if (("${#missing_modules[@]}")); then
  printf 'error: missing required pkg-config modules:\n' >&2
  printf '  %s\n' "${missing_modules[@]}" >&2
  printf 'Run %s/scripts/install_system_deps.sh first.\n' "$repo_root" >&2
  exit 1
fi

sctp_probe="$(mktemp "${TMPDIR:-/tmp}/srsran-er-sctp-link.XXXXXX")"
if ! printf 'int main() { return 0; }\n' |
    c++ -x c++ - -lsctp -o "$sctp_probe" >/dev/null 2>&1; then
  rm -f -- "$sctp_probe"
  die "the SCTP runtime cannot be linked; install libsctp-dev"
fi
rm -f -- "$sctp_probe"

dpdk_version=""
dpdk_prefix=""
if $enable_dpdk; then
  command -v dpkg >/dev/null 2>&1 ||
    die "--dpdk requires dpkg for the version check"
  pkg-config --exists libdpdk ||
    die "--dpdk requires libdpdk-dev (DPDK >= 22.11)"
  dpdk_version="$(pkg-config --modversion libdpdk)"
  dpdk_prefix="$(pkg-config --variable=prefix libdpdk)"
  dpdk_minimum="22.11"
  dpkg --compare-versions "$dpdk_version" ge "$dpdk_minimum" ||
    die "--dpdk requires DPDK >= $dpdk_minimum; found $dpdk_version"
  printf 'DPDK preflight: requested, version %s, prefix %s\n' \
    "$dpdk_version" "${dpdk_prefix:-unknown}"
else
  printf 'DPDK preflight: not requested.\n'
fi
if $enable_uhd; then
  pkg-config --exists uhd ||
    die "--uhd requires libuhd-dev and uhd-host"
fi
if $enable_numa; then
  printf '#include <numa.h>\n' | c++ -E -x c++ - >/dev/null 2>&1 ||
    die "--numa requires libnuma-dev"
fi

linker_args=()
if $use_mold; then
  command -v mold >/dev/null 2>&1 ||
    die "--mold was requested, but mold is not installed"
  linker_args=(
    "-DCMAKE_EXE_LINKER_FLAGS=-fuse-ld=mold"
    "-DCMAKE_SHARED_LINKER_FLAGS=-fuse-ld=mold"
    "-DCMAKE_MODULE_LINKER_FLAGS=-fuse-ld=mold"
  )
fi

case "$(uname -m)" in
  x86_64)
    march="x86-64-v2"
    mtune="generic"
    ;;
  aarch64)
    march="armv8-a"
    mtune="generic"
    ;;
  *)
    die "unsupported build architecture: $(uname -m)"
    ;;
esac
if $use_native_arch; then
  march="native"
  mtune="native"
fi

protoc_path="$(realpath -- "$(command -v protoc)")"
"${repo_root}/scripts/check_protobuf_toolchain.sh" --protoc "$protoc_path"

protobuf_version="$(pkg-config --modversion protobuf)"
protobuf_prefix="$(pkg-config --variable=prefix protobuf)"
protobuf_include="$(pkg-config --variable=includedir protobuf)"
protobuf_libdir="$(pkg-config --variable=libdir protobuf)"
protobuf_library="${protobuf_libdir}/libprotobuf.so"
[[ -e "$protobuf_library" ]] ||
  die "canonical Protobuf linker library is missing: $protobuf_library"
protobuf_library="$(realpath -- "$protobuf_library")"
printf 'Preflight complete.\n'
if $clean && [[ -d "$build_dir" ]]; then
  printf 'Cleaning verified build directory: %s\n' "$build_dir"
  cmake -E remove_directory "$build_dir"
fi

cmake_bool() {
  if "$1"; then
    printf 'ON\n'
  else
    printf 'OFF\n'
  fi
}

cmake_args=(
  -S "$repo_root"
  -B "$build_dir"
  "-DCMAKE_BUILD_TYPE=$build_type"
  "-DBUILD_TESTING=OFF"
  "-DENABLE_ZEROMQ=ON"
  "-DENABLE_FFTW=ON"
  "-DENABLE_DPDK=$(cmake_bool "$enable_dpdk")"
  "-DENABLE_UHD=$(cmake_bool "$enable_uhd")"
  "-DENABLE_LIBNUMA=$(cmake_bool "$enable_numa")"
  "-DENABLE_MKL=OFF"
  "-DENABLE_ARMPL=OFF"
  "-DENABLE_PLUGINS=OFF"
  "-DMARCH=$march"
  "-DMTUNE=$mtune"
  "-DProtobuf_PROTOC_EXECUTABLE=$protoc_path"
  "-DProtobuf_INCLUDE_DIR=$protobuf_include"
  "-DProtobuf_LIBRARY=$protobuf_library"
  "-DEXPECTED_PROTOBUF_PREFIX=$protobuf_prefix"
  "-DEXPECTED_PROTOBUF_VERSION=$protobuf_version"
  "-DEXPECTED_PROTOBUF_INCLUDE_DIR=$protobuf_include"
  "-DEXPECTED_PROTOBUF_LIBRARY_DIR=$protobuf_libdir"
)
cmake_args+=("${linker_args[@]}")

printf 'Repository: %s\n' "$repo_root"
printf 'Build directory: %s\n' "$build_dir"
printf 'Build type: %s; jobs: %s; architecture: %s/%s\n' \
  "$build_type" "$jobs" "$march" "$mtune"
printf 'Configuring CMake (DPDK=%s).\n' "$(cmake_bool "$enable_dpdk")"
cmake "${cmake_args[@]}"
printf '%s\n' "$repo_root" >"$marker_file"
cache_value() {
  sed -n "s/^$1:[^=]*=//p" "${build_dir}/CMakeCache.txt" | tail -n 1
}

configured_dpdk="$(cache_value ENABLE_DPDK)"
if $enable_dpdk; then
  [[ "$configured_dpdk" == "ON" ]] ||
    die "CMake did not retain ENABLE_DPDK=ON (found '${configured_dpdk:-unset}')"
  detected_dpdk="$(cache_value DPDK_FOUND)"
  case "$detected_dpdk" in
    1|ON|TRUE|YES)
      ;;
    *)
      die "CMake did not detect DPDK after it was explicitly requested"
      ;;
  esac
  printf 'DPDK configuration verified: enabled, version %s, prefix %s\n' \
    "$dpdk_version" "${dpdk_prefix:-unknown}"
elif [[ "$configured_dpdk" != "OFF" ]]; then
  die "CMake did not retain ENABLE_DPDK=OFF (found '${configured_dpdk:-unset}')"
fi

if $configure_only; then
  printf 'Configuration complete: %s\n' "$build_dir"
  printf 'C++ Protobuf bindings will be generated under lib/edgeric when a target is built.\n'
  exit 0
fi

printf 'Building target gnb with %s parallel jobs.\n' "$jobs"
cmake --build "$build_dir" --target gnb --parallel "$jobs"

gnb_binary="${build_dir}/apps/gnb/gnb"
[[ -x "$gnb_binary" ]] || die "build completed without expected binary: $gnb_binary"

printf 'gNB binary: %s\n' "$gnb_binary"
printf 'CMake: %s\n' "$(cmake --version | sed -n '1p')"
printf 'Compiler: %s\n' "$(c++ --version | sed -n '1p')"
if $use_mold; then
  printf 'Linker: %s\n' "$(mold --version | sed -n '1p')"
else
  printf 'Linker: %s\n' "$(ld --version | sed -n '1p')"
fi
printf 'Protobuf: %s at %s\n' "$protobuf_version" "$protoc_path"
