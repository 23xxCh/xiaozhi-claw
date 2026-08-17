# Hensun Desk server deployment

This directory contains the non-secret deployment definition for the existing
1Panel server. Source is developed locally and released to versioned folders
under `/opt/hensun-desk/releases`. Runtime secrets, logs, backups and firmware
artifacts live under `/data/hensun-desk`.

The server Compose file reuses the existing MySQL 8.4 container through the
private `1panel-network`. It does not start MySQL or Redis and publishes only
three localhost ports:

- `127.0.0.1:13000` — Next.js web console
- `127.0.0.1:13001` — FastAPI control plane
- `127.0.0.1:13002` — realtime WSS gateway

The existing 1Panel OpenResty terminates TLS and routes public requests to
those ports. The staging hostname uses same-origin routing so the exact same
web image can later be promoted without rebuilding it.

Before starting a release, the operator must create the dedicated MySQL
database/user in 1Panel and fill `/data/hensun-desk/runtime/server.env` from
`server.env.example`. The filled file must remain server-only with mode `0600`.

## Manual staging release

The GitHub Actions **Deploy reviewed staging release** workflow only runs after
the `staging` environment approval and the exact `DEPLOY_STAGING` confirmation.
It uploads a secret-free archive, then invokes `release-staging.sh` on the
server. That script accepts only `staging.hensun-desk.top` and fails before
building if any public URL points at the formal hostname.

The server-only `server.env` for a staging release must contain these values:

```text
HENSUN_DEPLOY_TARGET=staging
HENSUN_STAGING_HOSTNAME=staging.hensun-desk.top
WEB_APP_URL=https://staging.hensun-desk.top
DEVICE_WS_URL=wss://staging.hensun-desk.top/v1/device/ws
OTA_BASE_URL=https://staging.hensun-desk.top/v1/ota/
HENSUN_MYSQL_BACKUP_COMMAND=<server-only command which writes gzip SQL to $HENSUN_MYSQL_BACKUP_FILE>
```

The backup command is intentionally server-specific: it must use the existing
1Panel MySQL container/account, and must not be added to GitHub secrets,
workflow output, or this repository. Before containers are updated, the script
backs up MySQL, checks Compose and migrations, waits for local plus HTTPS
health checks, then changes `/opt/hensun-desk/current`. On failure it restores
the old target and its tagged images. It never routes or deploys
`hensun-desk.top`.

No OpenResty configuration in this directory may be installed before the
corresponding test or formal publication confirmation.

The optional `HENSUN_DEBIAN_MIRROR`, `HENSUN_DEBIAN_SECURITY_MIRROR` and
`HENSUN_PIP_INDEX_URL` variables change only the package sources used while
building the backend image. When omitted, Docker uses the official Debian and
PyPI indexes. The current China-hosted server uses the Tsinghua mirrors to
avoid slow official downloads.
