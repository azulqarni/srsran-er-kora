#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/.." && pwd -P)"
requirements_dir="${repo_root}/edgeric-v2/requirements"

python_request="${PYTHON:-python3}"
python_was_explicit=false
if [[ -n "${PYTHON:-}" ]]; then
  python_was_explicit=true
fi
# Keep all Python and pip work inside the selected venv.
unset PYTHONHOME PYTHONPATH PIP_USER PIP_TARGET PIP_PREFIX PIP_ROOT
export PYTHONNOUSERSITE=1
export PIP_CONFIG_FILE=/dev/null
venv_request=".venv"
with_muapps=false
generate_only=false
smoke_only=false
clean_only=false

usage() {
  cat <<'USAGE'
Usage: scripts/setup_edgeric_venv.sh [options]

Create/update an isolated EdgeRIC environment, generate canonical Python
Protobuf bindings, and run import/serialization smoke checks.

Options:
  --python PATH      Interpreter used to create the venv (default: $PYTHON or python3)
  --venv PATH        Repository-local venv path (default: .venv)
  --with-muapps      Install the full legacy muApp/RL/plotting dependency set
  --generate-only    Regenerate bindings in an already-created venv, then smoke-test
  --smoke-only       Run smoke checks in an already-created venv
  --clean            Remove only the selected, verified repository venv
  -h, --help         Show this help

The base environment supports CPython 3.10-3.12 and covers EdgeRIC messaging,
testers, and the slice telemetry/control muApps. --with-muapps requires
CPython 3.10 or 3.11 because protected legacy code imports the removed stdlib
imp module and uses NumPy's removed np.int alias.
USAGE
}

while (($#)); do
  case "$1" in
    --python)
      (($# >= 2)) || { printf 'error: --python requires a value\n' >&2; exit 2; }
      python_request="$2"
      python_was_explicit=true
      shift 2
      ;;
    --venv)
      (($# >= 2)) || { printf 'error: --venv requires a value\n' >&2; exit 2; }
      venv_request="$2"
      shift 2
      ;;
    --with-muapps)
      with_muapps=true
      shift
      ;;
    --generate-only)
      generate_only=true
      shift
      ;;
    --smoke-only)
      smoke_only=true
      shift
      ;;
    --clean)
      clean_only=true
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

mode_count=0
$generate_only && ((mode_count += 1))
$smoke_only && ((mode_count += 1))
$clean_only && ((mode_count += 1))
if ((mode_count > 1)); then
  printf 'error: --generate-only, --smoke-only, and --clean are mutually exclusive.\n' >&2
  exit 2
fi

for required_tool in git realpath; do
  command -v -- "$required_tool" >/dev/null 2>&1 || {
    printf 'error: %s is required; run scripts/install_system_deps.sh.\n' "$required_tool" >&2
    exit 1
  }
done

if [[ "$venv_request" = /* ]]; then
  venv_dir="$(realpath -m -- "$venv_request")"
else
  venv_dir="$(realpath -m -- "${repo_root}/${venv_request}")"
fi
case "$venv_dir" in
  "${repo_root}/"*)
    ;;
  *)
    printf 'error: venv must remain inside the repository: %s\n' "$venv_dir" >&2
    exit 1
    ;;
esac
[[ "$venv_dir" != "$repo_root" ]] || {
  printf 'error: refusing to use the repository root as a venv.\n' >&2
  exit 1
}
venv_relative="${venv_dir#"${repo_root}/"}"
if [[ -n "$(git -C "$repo_root" ls-files -- "$venv_relative")" ]]; then
  printf 'error: refusing a venv path containing tracked files: %s\n' "$venv_dir" >&2
  exit 1
fi
git -C "$repo_root" check-ignore --quiet --no-index -- "${venv_relative}/pyvenv.cfg" || {
  printf 'error: venv path is not covered by repository ignore rules: %s\n' \
    "$venv_dir" >&2
  printf 'Use .venv, .venv-NAME, venv, env, or another explicitly ignored path.\n' >&2
  exit 1
}

if $clean_only; then
  if [[ ! -e "$venv_dir" ]]; then
    printf 'Venv is clean: %s\n' "$venv_dir"
    exit 0
  fi
  [[ -d "$venv_dir" && ! -L "$venv_dir" && -f "${venv_dir}/pyvenv.cfg" ]] || {
    printf 'error: refusing to remove an unverified venv path: %s\n' "$venv_dir" >&2
    exit 1
  }
  case "$venv_dir" in
    "${repo_root}/"*) rm -rf -- "$venv_dir" ;;
    *) printf 'error: refusing cleanup outside repository: %s\n' "$venv_dir" >&2; exit 1 ;;
  esac
  printf 'Removed verified venv: %s\n' "$venv_dir"
  exit 0
fi
resolve_python() {
  local requested="$1"
  local resolved
  if [[ "$requested" == *"/"* ]]; then
    [[ -x "$requested" ]] || {
      printf 'error: Python interpreter not executable: %s\n' "$requested" >&2
      return 1
    }
    resolved="$(realpath -- "$requested")"
  else
    resolved="$(command -v -- "$requested" 2>/dev/null || true)"
    [[ -n "$resolved" ]] || {
      printf 'error: Python interpreter not found: %s\n' "$requested" >&2
      return 1
    }
    resolved="$(realpath -- "$resolved")"
  fi
  printf '%s\n' "$resolved"
}

python_minor() {
  "$1" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")'
}

validate_python_version() {
  local python_bin="$1"
  local version
  local implementation
  implementation="$("$python_bin" -c 'import sys; print(sys.implementation.name)')"
  if [[ "$implementation" != "cpython" ]]; then
    printf 'error: EdgeRIC requires CPython; found %s at %s.\n' \
      "$implementation" "$python_bin" >&2
    return 1
  fi
  version="$(python_minor "$python_bin")"
  case "$version" in
    3.10|3.11|3.12)
      ;;
    *)
      printf 'error: EdgeRIC supports CPython 3.10-3.12; found %s at %s.\n' \
        "$version" "$python_bin" >&2
      return 1
      ;;
  esac
  if $with_muapps && [[ "$version" != "3.10" && "$version" != "3.11" ]]; then
    printf 'error: --with-muapps requires CPython 3.10 or 3.11; found %s.\n' \
      "$version" >&2
    printf 'The legacy sources import imp and use np.int; changing them is outside setup scope.\n' >&2
    return 1
  fi
}

validate_venv_isolation() {
  local cfg="${1}/pyvenv.cfg"
  if grep -Eiq '^[[:space:]]*include-system-site-packages[[:space:]]*=[[:space:]]*true[[:space:]]*$' "$cfg"; then
    printf 'error: venv enables system site-packages and is not isolated: %s\n' "$1" >&2
    return 1
  fi
}

if [[ -e "$venv_dir" ]]; then
  [[ -d "$venv_dir" && ! -L "$venv_dir" && -f "${venv_dir}/pyvenv.cfg" ]] || {
    printf 'error: existing path is not a verified venv: %s\n' "$venv_dir" >&2
    exit 1
  }
  validate_venv_isolation "$venv_dir"
  venv_python="${venv_dir}/bin/python"
  [[ -x "$venv_python" ]] || {
    printf 'error: venv Python is missing: %s\n' "$venv_python" >&2
    exit 1
  }
  if $python_was_explicit; then
    requested_python="$(resolve_python "$python_request")"
    validate_python_version "$requested_python"
    if [[ "$(python_minor "$requested_python")" != "$(python_minor "$venv_python")" ]]; then
      printf 'error: existing venv uses Python %s, but the requested Python is %s.\n' \
        "$(python_minor "$venv_python")" "$(python_minor "$requested_python")" >&2
      printf 'Choose another ignored --venv path or remove this verified venv manually.\n' >&2
      exit 1
    fi
  fi
  validate_python_version "$venv_python"
else
  if $generate_only || $smoke_only; then
    printf 'error: venv does not exist: %s\n' "$venv_dir" >&2
    printf 'Run scripts/setup_edgeric_venv.sh without --generate-only/--smoke-only first.\n' >&2
    exit 1
  fi
  requested_python="$(resolve_python "$python_request")"
  validate_python_version "$requested_python"
  "$requested_python" -m venv "$venv_dir"
  validate_venv_isolation "$venv_dir"
  venv_python="${venv_dir}/bin/python"
fi

smoke_args=()
$with_muapps && smoke_args+=(--with-muapps)

if $smoke_only; then
  PYTHONDONTWRITEBYTECODE=1 "$venv_python" -I -B \
    "${script_dir}/smoke_edgeric.py" "${smoke_args[@]}"
  exit 0
fi

if ! $generate_only; then
  export PIP_DISABLE_PIP_VERSION_CHECK=1
  export PIP_REQUIRE_VIRTUALENV=1
  "$venv_python" -I -m pip --isolated --require-virtualenv install --upgrade \
    --requirement "${requirements_dir}/packaging.txt"
  if $with_muapps; then
    "$venv_python" -I -m pip --isolated --require-virtualenv install --upgrade-strategy only-if-needed \
      --requirement "${requirements_dir}/muapps.txt"
  else
    "$venv_python" -I -m pip --isolated --require-virtualenv install --upgrade-strategy only-if-needed \
      --requirement "${requirements_dir}/base.txt"
  fi
  "$venv_python" -I -m pip --isolated --require-virtualenv check
fi

"${script_dir}/generate_python_protos.sh" --python "$venv_python"
PYTHONDONTWRITEBYTECODE=1 "$venv_python" -I -B \
  "${script_dir}/smoke_edgeric.py" "${smoke_args[@]}"

printf 'EdgeRIC venv is ready: %s\n' "$venv_dir"
printf 'Activate it with: source %q\n' "${venv_dir}/bin/activate"
if ! $with_muapps; then
  printf '%s\n' 'For the full legacy muApp stack, create a separate environment:' \
    '  scripts/setup_edgeric_venv.sh --venv .venv-muapps --python python3.11 --with-muapps'
fi
