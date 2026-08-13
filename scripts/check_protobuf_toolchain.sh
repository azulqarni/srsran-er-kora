#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/.." && pwd -P)"

python_request=""
protoc_request="${PROTOC:-protoc}"

usage() {
  cat <<'USAGE'
Usage: scripts/check_protobuf_toolchain.sh [options]

Checks that protoc, pkg-config, CMake headers, and the C++ runtime all come
from one Protobuf installation. If a Python interpreter is explicitly supplied,
it also checks the Python runtime.

Options:
  --protoc PATH   Protobuf compiler to inspect (default: $PROTOC or protoc)
  --python PATH   Python interpreter whose protobuf runtime should be checked
  -h, --help      Show this help
USAGE
}

while (($#)); do
  case "$1" in
    --protoc)
      (($# >= 2)) || { printf 'error: --protoc requires a value\n' >&2; exit 2; }
      protoc_request="$2"
      shift 2
      ;;
    --python)
      (($# >= 2)) || { printf 'error: --python requires a value\n' >&2; exit 2; }
      python_request="$2"
      shift 2
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

resolve_executable() {
  local requested="$1"
  local resolved
  if [[ "$requested" == *"/"* ]]; then
    [[ -x "$requested" ]] || {
      printf 'error: executable not found: %s\n' "$requested" >&2
      return 1
    }
    resolved="$(realpath -- "$requested")"
  else
    resolved="$(command -v -- "$requested" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || {
      printf 'error: required command not found: %s\n' "$requested" >&2
      return 1
    }
    resolved="$(realpath -- "$resolved")"
  fi
  printf '%s\n' "$resolved"
}

resolve_python_executable() {
  local requested="$1"
  local resolved
  if [[ "$requested" == *"/"* ]]; then
    resolved="$requested"
  else
    resolved="$(command -v -- "$requested" 2>/dev/null || true)"
  fi
  [[ -n "$resolved" && -x "$resolved" ]] || {
    printf 'error: Python executable not found: %s\n' "$requested" >&2
    return 1
  }

  # Preserve a venv's final python symlink; resolving it would bypass the venv.
  realpath --no-symlinks -- "$resolved"
}

for required_tool in cmake pkg-config realpath mktemp; do
  command -v -- "$required_tool" >/dev/null 2>&1 || {
    printf 'error: required tool not found: %s\n' "$required_tool" >&2
    printf 'Run %s/scripts/install_system_deps.sh first.\n' "$repo_root" >&2
    exit 1
  }
done

pkg-config --exists protobuf || {
  printf 'error: pkg-config cannot find protobuf; install libprotobuf-dev.\n' >&2
  exit 1
}

protoc_path="$(resolve_executable "$protoc_request")"
protoc_output="$("$protoc_path" --version)"
if [[ "$protoc_output" =~ ^libprotoc[[:space:]]+([0-9]+\.[0-9]+(\.[0-9]+)?)$ ]]; then
  protoc_version="${BASH_REMATCH[1]}"
else
  printf 'error: unexpected output from %s --version: %s\n' \
    "$protoc_path" "$protoc_output" >&2
  exit 1
fi

pkg_version="$(pkg-config --modversion protobuf)"
pkg_prefix="$(pkg-config --variable=prefix protobuf)"
pkg_include="$(pkg-config --variable=includedir protobuf)"
pkg_libdir="$(pkg-config --variable=libdir protobuf)"

[[ -n "$pkg_prefix" ]] || {
  printf 'error: protobuf pkg-config metadata has no installation prefix.\n' >&2
  exit 1
}
expected_protoc="${pkg_prefix}/bin/protoc"
[[ -x "$expected_protoc" ]] || {
  printf 'error: protobuf pkg-config prefix does not contain protoc: %s\n' \
    "$expected_protoc" >&2
  exit 1
}
expected_protoc="$(realpath -- "$expected_protoc")"
if [[ "$protoc_path" != "$expected_protoc" ]]; then
  printf 'error: mixed native Protobuf installation prefixes detected.\n' >&2
  printf '  requested protoc: %s\n' "$protoc_path" >&2
  printf '  pkg-config tool:  %s\n' "$expected_protoc" >&2
  printf '  pkg-config prefix: %s\n' "$pkg_prefix" >&2
  exit 1
fi

if [[ "$protoc_version" != "$pkg_version" ]]; then
  printf 'error: mixed native Protobuf toolchain detected.\n' >&2
  printf '  protoc:     %s (%s)\n' "$protoc_path" "$protoc_version" >&2
  printf '  pkg-config: %s\n' "$pkg_version" >&2
  printf 'Remove conflicting installations or make PATH and PKG_CONFIG_PATH consistent.\n' >&2
  exit 1
fi

printf 'Protobuf protoc path: %s\n' "$protoc_path"
printf 'Protobuf protoc version: %s\n' "$protoc_version"
printf 'pkg-config Protobuf version: %s\n' "$pkg_version"
printf 'pkg-config Protobuf prefix: %s\n' "$pkg_prefix"
printf 'pkg-config header/include location: %s\n' "$pkg_include"
printf 'pkg-config library location: %s\n' "$pkg_libdir"

tmp_base="${TMPDIR:-/tmp}"
[[ -d "$tmp_base" ]] || {
  printf 'error: temporary directory does not exist: %s\n' "$tmp_base" >&2
  exit 1
}
tmp_base="$(realpath -- "$tmp_base")"
probe_dir="$(mktemp -d "${tmp_base}/srsran-er-protobuf.XXXXXX")"
cleanup_probe() {
  case "$probe_dir" in
    "${tmp_base}"/srsran-er-protobuf.*)
      rm -rf -- "$probe_dir"
      ;;
    *)
      printf 'warning: refusing to remove unexpected probe path: %s\n' "$probe_dir" >&2
      ;;
  esac
}
trap cleanup_probe EXIT

cmake \
  -S "${repo_root}/scripts/cmake/protobuf_probe" \
  -B "$probe_dir" \
  -DSRSRAN_REPOSITORY_ROOT="$repo_root" \
  -DProtobuf_PROTOC_EXECUTABLE="$protoc_path" \
  -DEXPECTED_PROTOBUF_PREFIX="$pkg_prefix" \
  -DEXPECTED_PROTOBUF_VERSION="$pkg_version" \
  -DEXPECTED_PROTOBUF_INCLUDE_DIR="$pkg_include" \
  -DEXPECTED_PROTOBUF_LIBRARY_DIR="$pkg_libdir"

if [[ -z "$python_request" ]]; then
  printf 'Python Protobuf runtime: not checked (pass --python after venv setup)\n'
  exit 0
fi

python_path="$(resolve_python_executable "$python_request")"
"$python_path" -I - "$protoc_version" <<'PY'
import re
import sys

try:
    import google.protobuf
except ModuleNotFoundError as exc:
    raise SystemExit(
        f"error: {sys.executable} has no protobuf runtime; install requirements first"
    ) from exc

generator_text = sys.argv[1]
generator = tuple(int(part) for part in generator_text.split("."))
runtime_text = google.protobuf.__version__
match = re.fullmatch(r"(\d+)\.(\d+)(?:\.(\d+))?.*", runtime_text)
if not match:
    raise SystemExit(f"error: cannot parse Python protobuf runtime {runtime_text!r}")
runtime = tuple(int(part or 0) for part in match.groups())

if generator[0] == 3 and generator[1] >= 20:
    generator_release = generator[1]
elif generator[0] >= 20:
    generator_release = generator[0]
else:
    raise SystemExit(
        "error: Python bindings require protoc 3.20+; "
        f"found {generator_text}"
    )

if runtime[0] >= 4:
    runtime_release = runtime[1]
    if generator_release > runtime_release:
        raise SystemExit(
            "error: generated Python code would be newer than the protobuf runtime: "
            f"protoc release {generator_release}, runtime release {runtime_release}"
        )
elif generator > runtime:
    raise SystemExit(
        "error: generated Python code would be newer than the protobuf runtime: "
        f"protoc {generator_text}, runtime {runtime_text}"
    )

print(f"Python executable: {sys.executable}")
print(f"Python version: {sys.version.split()[0]}")
print(f"Python Protobuf runtime: {runtime_text}")
print(f"Python Protobuf module: {google.protobuf.__file__}")
print(
    "Python generator/runtime compatibility: supported "
    f"(protoc release {generator_release}, runtime {runtime_text})"
)
PY
