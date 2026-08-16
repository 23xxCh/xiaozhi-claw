# Hensun Desk test deployment manifest

Status: `TEST_DEPLOYMENT_CONFIRMED`

Confirmation: the user supplied the exact phrase `确认测试部署` on 2026-08-16.

## Source and release

- Project: `hensun-desk`
- Local source: `E:\AI TOY\xiaozhi-claw`
- Git branch: `feature/commercial-pilot-foundation`
- Git baseline: `d7446ec`
- Release source: a secret-free, versioned archive of the tested server files.
- Server code root: `/opt/hensun-desk`
- Data root: `/data/hensun-desk`

The worktree currently contains tested but uncommitted backend and firmware
changes. The server archive will include only backend/web/deployment files and
will record its SHA256; firmware build output and the unused voiceprint
experiment are excluded.

## Test stage

- Test hostname: `staging.hensun-desk.top`
- Formal hostname: `hensun-desk.top` (not changed in the test stage)
- DNS authority: Cloudflare
- Proposed DNS record: proxied `A staging` to the same origin IPv4 already used
  by the existing Hensun records.
- Current conflict check: the authoritative DNS response is NXDOMAIN; no
  existing staging record will be overwritten.
- TLS: Let's Encrypt certificate for the staging hostname, terminated by the
  existing 1Panel OpenResty.

Cloudflare DNS automation is not available from the current authenticated tool
path. If it remains unavailable after confirmation, the user must add only the
reviewed `staging` record in the Cloudflare dashboard; no other record changes.

## Runtime

| Component | Local binding | Health check | Limit |
|---|---|---|---|
| Next.js web | `127.0.0.1:13000` | `/` | 0.5 CPU / 768 MB |
| FastAPI control plane | `127.0.0.1:13001` | `/health/ready` | 0.5 CPU / 768 MB |
| FastAPI realtime gateway | `127.0.0.1:13002` | `/health/ready` | 1 CPU / 2 GB |

Aggregate ceiling: 2 CPU / 4 GB. No application port, MySQL port or Redis port
is exposed publicly. OpenResty is the only public ingress.

## Database and secrets

- Reuse the existing MySQL 8.4 container over private `1panel-network`.
- Create a dedicated `hensun_desk` database and dedicated least-privilege user
  through 1Panel. Do not read or reuse the MySQL master password.
- Redis is not used.
- The user enters runtime secrets into
  `/data/hensun-desk/runtime/server.env` with mode `0600`.
- Required production prerequisites are SMTP credentials, fresh AI provider
  keys, an OTA Ed25519 public key and generated application secrets.
- No `.env`, API key, private key, database password, device key, local SQLite
  database, raw audio or transcript is uploaded from the repository.

## OpenResty and TLS changes

Test confirmation authorizes only:

1. the staging vhost for `staging.hensun-desk.top`;
2. ACME certificate issuance for that hostname;
3. reverse proxy routes: `/` to web, `/v1/device/ws` to the gateway and other
   `/v1/` paths to the control plane;
4. an OpenResty syntax test and graceful reload.

The existing `cxxfxc.icu`, `i-love-abc.top`, `panel.i-love-abc.top`, MaxKB,
DeepSeek, MySQL, Redis and 1Panel services are protected and remain unchanged.

## Storage and retention

- Versioned sources: `/opt/hensun-desk/releases/<release-id>`.
- Runtime data: `/data/hensun-desk/runtime`.
- Backups: `/data/hensun-desk/backups`.
- Logs: `/data/hensun-desk/logs`, plus bounded Docker JSON logs.
- Firmware: `/data/hensun-desk/firmware`.
- Daily cleanup retains the current and three newest source releases, rotates
  project logs, and deletes unpinned firmware older than 90 days. Backups are
  not automatically deleted.

## Verification and rollback

Before switching the staging route:

- validate the Compose rendering;
- build the two images;
- run Alembic against the isolated database;
- require all three container health checks to pass;
- verify local HTTP and WSS upgrade behavior;
- validate OpenResty syntax.

After publication, verify the staging HTTPS page, login API, health endpoints,
WSS handshake, container limits and logs. The previous `/opt/hensun-desk/current`
target remains intact until all checks pass. Rollback restores that symlink and
the prior Compose image tags, then gracefully reloads OpenResty if needed.

Formal publication is a separate gate and requires the exact phrase
`确认正式发布`; the test stage does not authorize changes to the existing formal
Hensun DNS records or formal OpenResty vhosts.
