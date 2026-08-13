#!/usr/bin/env bash
set -Eeuo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
gnb_binary="${repo_root}/build/apps/gnb/gnb"
gnb_config="${1:-${repo_root}/configs/fns_4x4_100mhz_slicing.yaml}"
caller_user="${SUDO_USER:-$(id -un)}"
gnb_group="${GNB_GROUP:-$(id -gn "$caller_user")}"

for tool in sudo systemctl systemd-run; do
  command -v "$tool" >/dev/null 2>&1 || {
    printf 'error: required command not found: %s\n' "$tool" >&2
    exit 1
  }
done

[[ -x "$gnb_binary" ]] || {
  printf 'error: gNB binary not found: %s\n' "$gnb_binary" >&2
  printf 'Build it first with: %s/build-srs-gnb-er.sh\n' "$repo_root" >&2
  exit 1
}

[[ -f "$gnb_config" ]] || {
  printf 'error: gNB configuration not found: %s\n' "$gnb_config" >&2
  exit 1
}

read -r -a caller_groups <<<"$(id -Gn "$caller_user")"
group_member=false
for caller_group in "${caller_groups[@]}"; do
  if [[ "$caller_group" == "$gnb_group" ]]; then
    group_member=true
    break
  fi
done
$group_member || {
  printf 'error: user %s is not a member of group %s\n' \
    "$caller_user" "$gnb_group" >&2
  exit 1
}

printf 'Starting gNB as root with IPC group %s (umask 0007).\n' "$gnb_group"
printf 'Python muApps can run without sudo as user %s.\n' "$caller_user"

sudo systemctl reset-failed gnb.service 2>/dev/null || true
exec sudo systemd-run \
  --pty \
  --unit=gnb \
  --slice=gnb.slice \
  -p User=root \
  -p "Group=${gnb_group}" \
  -p UMask=0007 \
  -p "WorkingDirectory=${repo_root}" \
  -- \
  "$gnb_binary" -c "$gnb_config"
