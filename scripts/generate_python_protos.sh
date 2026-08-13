#!/usr/bin/env bash
set -euo pipefail

script_dir="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
repo_root="$(cd -- "${script_dir}/.." && pwd -P)"
proto_dir="${repo_root}/lib/protobufs"
output_dir="${repo_root}/edgeric-v2/protobufs"

python_request=""
protoc_request="${PROTOC:-protoc}"
clean_only=false
proto_names=(
  control_mcs
  control_weights
  metrics
  slice_budgets
  slice_metrics
)

usage() {
  cat <<'USAGE'
Usage: scripts/generate_python_protos.sh [options]

Generate disposable Python bindings from the canonical lib/protobufs schemas.

Options:
  --python PATH   Python interpreter with the protobuf runtime installed
  --protoc PATH   Protobuf compiler (default: $PROTOC or protoc)
  --clean         Remove only the known generated Python binding files
  -h, --help      Show this help
USAGE
}

while (($#)); do
  case "$1" in
    --python)
      (($# >= 2)) || { printf 'error: --python requires a value\n' >&2; exit 2; }
      python_request="$2"
      shift 2
      ;;
    --protoc)
      (($# >= 2)) || { printf 'error: --protoc requires a value\n' >&2; exit 2; }
      protoc_request="$2"
      shift 2
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

if $clean_only; then
  [[ -f "${output_dir}/__init__.py" ]] || {
    printf 'error: refusing cleanup because package marker is missing: %s/__init__.py\n' \
      "$output_dir" >&2
    exit 1
  }
  for proto_name in "${proto_names[@]}"; do
    rm -f -- \
      "${output_dir}/${proto_name}_pb2.py" \
      "${output_dir}/${proto_name}_pb2.pyi" \
      "${output_dir}/${proto_name}_pb2_grpc.py" \
      "${output_dir}/${proto_name}_pb2_grpc.pyi"
  done
  printf 'Removed known generated Python Protobuf bindings from %s\n' "$output_dir"
  printf 'Preserved %s/__init__.py and all archived schemas.\n' "$output_dir"
  exit 0
fi

if [[ -z "$python_request" ]]; then
  if [[ -n "${VIRTUAL_ENV:-}" && -x "${VIRTUAL_ENV}/bin/python" ]]; then
    python_request="${VIRTUAL_ENV}/bin/python"
  elif [[ -x "${repo_root}/.venv/bin/python" ]]; then
    python_request="${repo_root}/.venv/bin/python"
  else
    printf 'error: no EdgeRIC venv found; run scripts/setup_edgeric_venv.sh first.\n' >&2
    exit 1
  fi
fi

"${script_dir}/check_protobuf_toolchain.sh" \
  --protoc "$protoc_request" \
  --python "$python_request"

[[ -f "${output_dir}/__init__.py" ]] || {
  printf 'error: required package marker is missing: %s/__init__.py\n' "$output_dir" >&2
  exit 1
}

proto_files=()
for proto_name in "${proto_names[@]}"; do
  proto_file="${proto_dir}/${proto_name}.proto"
  [[ -f "$proto_file" ]] || {
    printf 'error: canonical schema missing: %s\n' "$proto_file" >&2
    exit 1
  }
  proto_files+=("$proto_file")
done

protoc_path="$(command -v -- "$protoc_request" 2>/dev/null || true)"
if [[ "$protoc_request" == *"/"* ]]; then
  protoc_path="$protoc_request"
fi
[[ -x "$protoc_path" ]] || {
  printf 'error: protoc executable not found: %s\n' "$protoc_request" >&2
  exit 1
}
protoc_path="$(realpath -- "$protoc_path")"

tmp_base="$(realpath -- "${TMPDIR:-/tmp}")"
generation_dir="$(mktemp -d "${tmp_base}/srsran-er-python-protobuf.XXXXXX")"
cleanup_generation() {
  case "$generation_dir" in
    "${tmp_base}"/srsran-er-python-protobuf.*)
      rm -rf -- "$generation_dir"
      ;;
    *)
      printf 'warning: refusing to remove unexpected generation path: %s\n' \
        "$generation_dir" >&2
      ;;
  esac
}
trap cleanup_generation EXIT

"$protoc_path" \
  --proto_path="$proto_dir" \
  --python_out="$generation_dir" \
  "${proto_files[@]}"

for proto_name in "${proto_names[@]}"; do
  generated="${generation_dir}/${proto_name}_pb2.py"
  [[ -s "$generated" ]] || {
    printf 'error: protoc did not create expected binding: %s\n' "$generated" >&2
    exit 1
  }
done

for proto_name in "${proto_names[@]}"; do
  install -m 0644 \
    "${generation_dir}/${proto_name}_pb2.py" \
    "${output_dir}/${proto_name}_pb2.py"
  rm -f -- \
    "${output_dir}/${proto_name}_pb2.pyi" \
    "${output_dir}/${proto_name}_pb2_grpc.py" \
    "${output_dir}/${proto_name}_pb2_grpc.pyi"
done

printf 'Generated Python Protobuf bindings from %s into %s\n' "$proto_dir" "$output_dir"
printf 'These files are ignored build output and must not be committed.\n'
