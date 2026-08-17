#!/usr/bin/env bash
set -euo pipefail

# Runs on the server after GitHub Actions has unpacked a reviewed release into
# /opt/hensun-desk/releases/<release-id>. It intentionally knows nothing about
# the formal hostname or formal OTA channel.

release_id="${1:?usage: release-staging.sh <release-id>}"
release_root=/opt/hensun-desk/releases
data_root=/data/hensun-desk
runtime_env="$data_root/runtime/server.env"
candidate="$release_root/$release_id"
lock_file="$data_root/runtime/staging-release.lock"

[[ "$release_id" =~ ^[A-Za-z0-9._-]{7,96}$ ]] || {
  echo "invalid release id" >&2
  exit 2
}
[[ -f "$candidate/deploy/docker-compose.server.yml" ]] || {
  echo "candidate release is incomplete: $candidate" >&2
  exit 2
}
[[ -f "$runtime_env" ]] || {
  echo "missing server-only runtime environment: $runtime_env" >&2
  exit 2
}
[[ "${HENSUN_DEPLOY_TARGET:-staging}" == "staging" ]] || {
  echo "this script may only deploy staging" >&2
  exit 2
}
[[ "${HENSUN_STAGING_HOSTNAME:-staging.hensun-desk.top}" == "staging.hensun-desk.top" ]] || {
  echo "unexpected staging hostname" >&2
  exit 2
}

mkdir -p "$data_root/backups/mysql" "$data_root/runtime"
exec 9>"$lock_file"
flock -n 9 || {
  echo "another staging release is already running" >&2
  exit 3
}

# Load server-only credentials without echoing any value. The command itself is
# configured only on the server because MySQL is managed by the existing 1Panel
# installation and its container name/credentials are deployment-specific.
set -a
# shellcheck disable=SC1090
source "$runtime_env"
set +a
: "${HENSUN_MYSQL_BACKUP_COMMAND:?set a server-only HENSUN_MYSQL_BACKUP_COMMAND}"
[[ "${WEB_APP_URL:-}" == "https://staging.hensun-desk.top" ]] || {
  echo "WEB_APP_URL must be the staging hostname for this release" >&2
  exit 2
}
[[ "${DEVICE_WS_URL:-}" == "wss://staging.hensun-desk.top/v1/device/ws" ]] || {
  echo "DEVICE_WS_URL must be the staging WSS hostname for this release" >&2
  exit 2
}
[[ "${OTA_BASE_URL:-}" == "https://staging.hensun-desk.top/v1/ota/" ]] || {
  echo "OTA_BASE_URL must be the staging hostname for this release" >&2
  exit 2
}

previous=""
if [[ -L /opt/hensun-desk/current ]]; then
  previous="$(readlink -f /opt/hensun-desk/current)"
fi

compose=(docker compose --project-name hensun-desk --env-file "$runtime_env" -f "$candidate/deploy/docker-compose.server.yml")
rollback() {
  local status=$?
  echo "staging release failed; restoring prior application target" >&2
  if [[ -n "$previous" && -f "$previous/deploy/docker-compose.server.yml" ]]; then
    local previous_id
    previous_id="$(basename "$previous")"
    HENSUN_RELEASE_ID="$previous_id" docker compose --project-name hensun-desk --env-file "$runtime_env" \
      -f "$previous/deploy/docker-compose.server.yml" up -d --no-build || true
    ln -sfn "$previous" /opt/hensun-desk/current
  fi
  exit "$status"
}
trap rollback ERR

backup_file="$data_root/backups/mysql/staging-before-${release_id}-$(date -u +%Y%m%dT%H%M%SZ).sql.gz"
export HENSUN_MYSQL_BACKUP_FILE="$backup_file"
bash -c "$HENSUN_MYSQL_BACKUP_COMMAND"
test -s "$backup_file"

HENSUN_RELEASE_ID="$release_id" "${compose[@]}" config -q
HENSUN_RELEASE_ID="$release_id" "${compose[@]}" build
HENSUN_RELEASE_ID="$release_id" "${compose[@]}" run --rm migrate
HENSUN_RELEASE_ID="$release_id" "${compose[@]}" up -d --no-build --remove-orphans

for endpoint in \
  http://127.0.0.1:13000/ \
  http://127.0.0.1:13001/health/ready \
  http://127.0.0.1:13002/health/ready \
  https://staging.hensun-desk.top/health/ready; do
  for attempt in {1..30}; do
    if curl --fail --silent --show-error --max-time 5 "$endpoint" >/dev/null; then
      break
    fi
    if [[ "$attempt" == 30 ]]; then
      echo "health check failed: $endpoint" >&2
      exit 1
    fi
    sleep 2
  done
done

printf '%s\n' "$release_id" > "$candidate/.release-id"
ln -sfn "$candidate" /opt/hensun-desk/current
trap - ERR
echo "staging release is healthy: $release_id"
