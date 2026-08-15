#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/.." && pwd -P)"
venv_dir="${repo_root}/.venv-edgeric-legacy"
requirements_dir="${repo_root}/edgeric-v2/requirements"
python_request="${PYTHON:-python3.11}"
protoc_request="${PROTOC:-protoc}"
mode=setup

usage() {
  cat <<'USAGE'
Usage: scripts/setup_legacy_edgeric_venv.sh [options]

Create a disposable environment for legacy EdgeRIC muApps 1-3.

  --python PATH   CPython 3.10/3.11 for the venv (default: python3.11)
  --smoke-only    Recheck the existing environment without reinstalling
  --clean         Remove the verified environment and all setup/smoke artifacts
  -h, --help      Show this help

The fixed environment is .venv-edgeric-legacy. Package pins are in
edgeric-v2/requirements/muapps.txt. The smoke check does not run live muApps.
Redis-backed paths require a separately managed redis-server.
USAGE
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 1
}

while (($#)); do
  case "$1" in
    --python)
      (($# >= 2)) || die "--python requires a value"
      python_request="$2"
      shift 2
      ;;
    --smoke-only|--clean)
      [[ "$mode" == setup ]] || die "--smoke-only and --clean are mutually exclusive"
      [[ "$1" == --smoke-only ]] && mode=smoke || mode=clean
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

for tool in git realpath; do
  command -v -- "$tool" >/dev/null 2>&1 || die "required tool not found: $tool"
done
[[ ! -L "$venv_dir" ]] || die "refusing symlink legacy venv: $venv_dir"
marker="${venv_dir}/.srsran-er-legacy-venv"
[[ -z "$(git -C "$repo_root" ls-files -- .venv-edgeric-legacy)" ]] ||
  die "legacy venv path contains tracked files"
git -C "$repo_root" check-ignore --quiet --no-index -- \
  .venv-edgeric-legacy/pyvenv.cfg || die "legacy venv path is not ignored"

verify_owned() {
  [[ -d "$venv_dir" && ! -L "$venv_dir" && -f "$marker" ]] ||
    die "legacy venv is missing or unverified: $venv_dir"
  [[ "$(sed -n '1p' "$marker")" == "$repo_root" ]] ||
    die "legacy venv belongs to another source tree"
}

validate_python() {
  local python_bin="$1" implementation version
  [[ -x "$python_bin" ]] || die "Python interpreter is not executable: $python_bin"
  implementation="$("$python_bin" -I -c 'import sys; print(sys.implementation.name)')"
  version="$("$python_bin" -I -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')"
  [[ "$implementation" == cpython ]] || die "legacy muApps require CPython"
  case "$version" in
    3.10|3.11) ;;
    *)
      die "legacy muApps require CPython 3.10/3.11; found $version
The sources use the removed imp module and NumPy np.int alias.
Provide an external supported interpreter with venv support, then rerun:
  scripts/setup_legacy_edgeric_venv.sh --python /path/to/python3.11
That interpreter is not managed or removed by --clean."
      ;;
  esac
}

verify_venv() {
  verify_owned
  [[ -f "${venv_dir}/pyvenv.cfg" && -x "${venv_dir}/bin/python" ]] ||
    die "legacy venv is incomplete"
  ! grep -Eiq '^[[:space:]]*include-system-site-packages[[:space:]]*=[[:space:]]*true' \
    "${venv_dir}/pyvenv.cfg" || die "legacy venv is not isolated"
}

if [[ "$mode" == clean ]]; then
  [[ -e "$venv_dir" ]] || {
    printf 'Legacy EdgeRIC environment is clean: %s\n' "$venv_dir"
    exit 0
  }
  verify_owned
  printf 'Removing verified legacy EdgeRIC environment: %s\n' "$venv_dir"
  rm -rf -- "$venv_dir"
  exit 0
fi

unset PYTHONHOME PYTHONPATH PIP_USER PIP_TARGET PIP_PREFIX PIP_ROOT
export PYTHONNOUSERSITE=1 PIP_CONFIG_FILE=/dev/null
venv_python="${venv_dir}/bin/python"
new_venv=false
if [[ -e "$venv_dir" ]]; then
  verify_venv
  validate_python "$venv_python"
elif [[ "$mode" == smoke ]]; then
  die "legacy venv does not exist: $venv_dir"
else
  python_bin="$python_request"
  [[ "$python_request" == */* ]] ||
    python_bin="$(command -v -- "$python_request" 2>/dev/null || true)"
  [[ -n "$python_bin" ]] || die "CPython interpreter not found: $python_request"
  validate_python "$python_bin"
  new_venv=true
fi

protoc_path=""
if [[ "$mode" == setup ]]; then
  protoc_path="$protoc_request"
  [[ "$protoc_request" == */* ]] ||
    protoc_path="$(command -v -- "$protoc_request" 2>/dev/null || true)"
  [[ -n "$protoc_path" && -x "$protoc_path" ]] ||
    die "protoc is missing; run scripts/install_system_deps.sh first"
  protoc_path="$(realpath -- "$protoc_path")"
  "${script_dir}/check_protobuf_toolchain.sh" --protoc "$protoc_path"
fi

if $new_venv; then
  mkdir -p -- "$venv_dir"
  printf '%s\n' "$repo_root" >"$marker"
fi
state_dir="${venv_dir}/.setup-state"
[[ ! -L "$state_dir" ]] || die "refusing symlink setup-state path"
install -d -m 0700 \
  "${state_dir}/home" "${state_dir}/pip-cache" "${state_dir}/tmp" "${state_dir}/xdg"
export HOME="${state_dir}/home" XDG_CACHE_HOME="${state_dir}/xdg"
export TMPDIR="${state_dir}/tmp" TMP="${state_dir}/tmp" TEMP="${state_dir}/tmp"

if $new_venv; then
  if ! "$python_bin" -I -m venv "$venv_dir"; then
    rm -rf -- "$venv_dir"
    die "failed to create legacy venv"
  fi
  verify_venv
fi

run_tmp="$(mktemp -d "${state_dir}/tmp/run.XXXXXX")"
trap 'rm -rf -- "$run_tmp"' EXIT
export TMPDIR="$run_tmp" TMP="$run_tmp" TEMP="$run_tmp" MPLBACKEND=Agg
generated_root="${venv_dir}/generated-python"
[[ ! -L "$generated_root" ]] || die "refusing symlink generated-binding path"
site_packages="$("$venv_python" -I -c 'import sysconfig; print(sysconfig.get_path("purelib"))')"
case "$(realpath -m -- "$site_packages")" in
  "${venv_dir}/"*) ;;
  *) die "venv site-packages escaped the verified environment" ;;
esac

run_smoke() {
  [[ -d "${generated_root}/protobufs" ]] || die "venv-local bindings are missing"
  (
    cd "$run_tmp"
    PYTHONDONTWRITEBYTECODE=1 "$venv_python" -I -B - \
      "$repo_root" "$generated_root" <<'PY'
import importlib
from pathlib import Path
import sys

repo, generated = map(lambda value: Path(value).resolve(), sys.argv[1:3])
for name in (
    "google.protobuf", "numpy", "zmq", "gym", "hydra", "kaleido",
    "matplotlib.pyplot", "pandas", "PIL.Image", "plotly.express", "ray.rllib",
    "redis", "scipy.optimize", "torch", "stream_rl", "edgeric_messenger",
    "muApp1.muApp1_dummy_single_ue", "muApp3.muApp3_bitrate_monitor",
):
    importlib.import_module(name)

from protobufs import control_mcs_pb2, control_weights_pb2, metrics_pb2
messages = (
    control_mcs_pb2.mcs_control,
    control_weights_pb2.SchedulingWeights,
    metrics_pb2.Metrics,
)
for message_type in messages:
    if not Path(sys.modules[message_type.__module__].__file__).resolve().is_relative_to(generated):
        raise RuntimeError(f"binding escaped legacy venv: {message_type.__module__}")
    original = message_type()
    decoded = message_type()
    decoded.ParseFromString(original.SerializeToString())
    if decoded != original:
        raise RuntimeError(f"round-trip failed for {message_type.__name__}")
for app in ("muApp1", "muApp2", "muApp3"):
    for source in sorted((repo / "edgeric-v2" / app).glob("*.py")):
        compile(source.read_bytes(), str(source), "exec")

import plotly.graph_objects as go
import plotly.io as pio
if not pio.to_image(go.Figure(), format="png", width=64, height=64).startswith(b"\x89PNG"):
    raise RuntimeError("Plotly/Kaleido image export failed")
print("Legacy EdgeRIC dependency/import smoke check: PASS")
PY
  )
}

if [[ "$mode" == smoke ]]; then
  "$venv_python" -I -m pip --isolated --require-virtualenv \
    --disable-pip-version-check check
  run_smoke
  exit 0
fi

pip_common=(
  -I -m pip --isolated --require-virtualenv --disable-pip-version-check
  --no-input --cache-dir "${state_dir}/pip-cache"
)
"$venv_python" "${pip_common[@]}" install --upgrade \
  --requirement "${requirements_dir}/packaging.txt"
"$venv_python" "${pip_common[@]}" install --upgrade-strategy only-if-needed \
  --requirement "${requirements_dir}/muapps.txt"
"$venv_python" "${pip_common[@]}" check
"${script_dir}/check_protobuf_toolchain.sh" \
  --protoc "$protoc_path" --python "$venv_python"

stage_package="${run_tmp}/generated-python/protobufs"
mkdir -p -- "$stage_package"
install -m 0644 "${repo_root}/edgeric-v2/protobufs/__init__.py" \
  "${stage_package}/__init__.py"
proto_paths=()
for name in control_mcs control_weights metrics; do
  proto_path="${repo_root}/lib/protobufs/${name}.proto"
  [[ -f "$proto_path" ]] || die "canonical schema is missing: $proto_path"
  proto_paths+=("$proto_path")
done
"$protoc_path" --proto_path="${repo_root}/lib/protobufs" \
  --python_out="$stage_package" "${proto_paths[@]}"
for name in control_mcs control_weights metrics; do
  [[ -s "${stage_package}/${name}_pb2.py" ]] || die "protoc omitted ${name}_pb2.py"
done
rm -rf -- "$generated_root"
mkdir -p -- "$generated_root"
mv -- "$stage_package" "${generated_root}/protobufs"

pth_file="${site_packages}/srsran_er_legacy.pth"
"$venv_python" -I - "$pth_file" "$generated_root" "${repo_root}/edgeric-v2" <<'PY'
from pathlib import Path
import sys
Path(sys.argv[1]).write_text(
    f"import sys; sys.path.insert(0, {sys.argv[2]!r})\n{sys.argv[3]}\n",
    encoding="utf-8",
)
PY

run_smoke
printf 'Legacy EdgeRIC environment: %s\n' "$venv_dir"
printf 'Activate: source %q\n' "${venv_dir}/bin/activate"
printf 'Remove setup/smoke artifacts: %q --clean\n' "$0"
if ! command -v redis-server >/dev/null 2>&1; then
  printf '%s\n' 'Redis-backed paths need redis-server; it is not installed by this script.'
fi
