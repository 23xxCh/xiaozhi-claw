#!/usr/bin/env bash
set -euo pipefail

data_root=/data/hensun-desk
release_root=/opt/hensun-desk/releases
current_target=""

if [[ -L /opt/hensun-desk/current ]]; then
  current_target="$(readlink -f /opt/hensun-desk/current)"
fi

# Application logs are already bounded by Docker's max-size/max-file options.
# This catches any diagnostic files explicitly written beneath the project tree.
find "$data_root/logs" -xdev -type f -mtime +14 -delete 2>/dev/null || true

# Firmware listed in keep.txt is retained for rollback regardless of age.
if [[ -d "$data_root/firmware" ]]; then
  while IFS= read -r -d '' artifact; do
    name="$(basename "$artifact")"
    if [[ -f "$data_root/firmware/keep.txt" ]] \
      && grep -Fqx -- "$name" "$data_root/firmware/keep.txt"; then
      continue
    fi
    rm -f -- "$artifact"
  done < <(find "$data_root/firmware" -xdev -maxdepth 1 -type f \
    ! -name keep.txt -mtime +90 -print0 2>/dev/null)
fi

# Keep the current release and the three newest release directories.
if [[ -d "$release_root" ]]; then
  mapfile -t releases < <(find "$release_root" -mindepth 1 -maxdepth 1 -type d -printf '%T@ %p\n' \
    | sort -nr | cut -d' ' -f2-)
  for ((i=3; i<${#releases[@]}; i++)); do
    candidate="$(readlink -f "${releases[$i]}")"
    if [[ "$candidate" == "$release_root"/* && "$candidate" != "$current_target" ]]; then
      rm -rf --one-file-system -- "$candidate"
    fi
  done
fi

# Remove only dangling Hensun images older than two weeks. Other projects and
# tagged rollback images remain untouched.
docker image prune -f \
  --filter 'label=com.hensun.project=hensun-desk' \
  --filter 'until=336h' >/dev/null
