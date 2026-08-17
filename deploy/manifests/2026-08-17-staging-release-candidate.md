# Hensun Desk staging release candidate

Status: `PREFLIGHT_COMPLETE_AWAITING_TEST_DEPLOYMENT_CONFIRMATION`

## Exact scope

- Target server: `hubei` (`codex-hubei-root`)
- Project: `hensun-desk`
- Test hostname: `staging.hensun-desk.top`
- Formal hostname: `hensun-desk.top` — not changed by this release.
- Source branch: `feature/hensun-stability-quality`
- Candidate baseline: `587987a`, plus the staged health-route correction in
  this release candidate.

## Read-only preflight evidence (2026-08-17)

- Docker, Docker Compose, Python, curl, and the Cloudflare Tunnel service are
  available on `hubei`.
- `hensun-desk-web-1`, `hensun-desk-control-api-1`, and
  `hensun-desk-realtime-gateway-1` are currently healthy.
- Application ports are loopback-only: web `13000`, control plane `13001`,
  realtime gateway `13002`.
- `staging.hensun-desk.top` resolves through Cloudflare and the public root
  page returns HTTP 200.
- The control plane and realtime gateway each return HTTP 200 from their local
  `/health/live` endpoint.

## Required staging-only correction

The existing staging OpenResty configuration directs `/health/ready` to the
frontend, which returns HTTP 404. This release adds an exact `/health/ready`
route to the control plane in both the TLS and Cloudflare Tunnel listener
blocks. During deployment, the release script will:

1. back up the existing staging-only vhost under
   `/data/hensun-desk/backups/openresty/`;
2. install the reviewed candidate vhost;
3. run `openresty -t` and perform a graceful reload;
4. restore the backup during automated rollback if any later release gate
   fails.

No formal-domain vhost, Cloudflare DNS record, Cloudflare Tunnel route,
database credential, device key, model key, or unrelated 1Panel service is
changed.

## Runtime and safety gates

| Component | Binding | Health check | Limit |
| --- | --- | --- | --- |
| Web | `127.0.0.1:13000` | `/` | 0.5 CPU / 768 MB |
| Control plane | `127.0.0.1:13001` | `/health/ready` | 0.5 CPU / 768 MB |
| Realtime gateway | `127.0.0.1:13002` | `/health/ready` | 1 CPU / 2 GB |

- MySQL remains private on the existing Docker network; Redis is not used.
- The server-only runtime environment remains in
  `/data/hensun-desk/runtime/server.env` and is never read or packaged.
- Before migration, the release script creates a MySQL backup in
  `/data/hensun-desk/backups/mysql/`.
- The old active release is kept as the rollback target. The release fails and
  restores it if local or public health checks fail.

## Approval needed

Proceed only after the owner supplies the exact phrase:

```text
确认测试部署到 hubei 的 staging.hensun-desk.top
```

This authorizes this staging release only. Formal publication remains a
separate gate.
