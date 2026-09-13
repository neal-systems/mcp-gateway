# Following three requests through the evidence

All excerpts below are from the live demo host (release B, image digest
`sha256:a66e9f1d...`), collected on 2026-09-13 with
`scripts/cloud/ops/evidence.sh` and targeted `docker logs` greps over
Systems Manager. Identifiers are as recorded; nothing here is a secret. The
raw bundle stays under `evidence/raw/` and is not published.

## 1. One allowed request: an operator calls `service_details`

The chain is request id -> trace id -> HTTP span -> tool span -> log line.

Log line for the tool call (JSON, redacted at source):

```
{"ts": "2026-09-13T19:18:17.651483+00:00", "level": "INFO",
 "logger": "mcp_gateway.telemetry", "msg": "tool call",
 "request_id": "f98100cc-334a-467f-8204-2d00b0f8f96d",
 "trace_id": "bac32589dfd7c79953df56bb9c159c0c", "span_id": "c8ab40a044b48245",
 "tool": "service_details", "outcome": "ok", "duration_ms": ...}
```

The tool span with the same trace id and span id:

```
{"otel": "span", "name": "mcp.tool", "trace_id": "bac32589dfd7c79953df56bb9c159c0c",
 "span_id": "c8ab40a044b48245", "parent_id": "f562de995b761be8",
 "attributes": {"mcp.tool.name": "service_details", "gateway.outcome": "ok"}}
```

Its parent, the HTTP server span (`parent_id` above equals this `span_id`):

```
{"otel": "span", "name": "http.request", "trace_id": "bac32589dfd7c79953df56bb9c159c0c",
 "span_id": "f562de995b761be8", "parent_id": null,
 "attributes": {"http.request.method": "POST", "http.route": "/mcp", ...}}
```

The identity decision that made the call allowed, logged without the
identity itself:

```
{"ts": "2026-09-13T19:18:18.073919+00:00", "level": "INFO", "logger": "mcp_gateway",
 "msg": "GitHub identity admitted with role operator", ...}
```

Metrics export after these calls: `gateway.tool.calls{tool=service_details,
outcome=ok}` incremented, `gateway.ready` = 1. No tool argument or result
appears anywhere in the chain: the span carries the tool name and the
outcome, the log line carries the duration.

## 2. One denied request: `POST /mcp` with no token

Sent from the internet with `X-Request-ID: evidence-denied-1` so the chain is
easy to find. Three records share the request id and trace id
`59765b407b7e5cf26ca73e1a6f56a31e`:

```
{"ts": "2026-09-13T19:22:46.907645+00:00", "level": "INFO", "logger": "uvicorn.access",
 "msg": "172.18.0.2:35106 - \"POST /mcp HTTP/1.1\" 401",
 "request_id": "evidence-denied-1", "trace_id": "59765b407b7e5cf26ca73e1a6f56a31e", "span_id": "d6a726686b915316"}
{"ts": "2026-09-13T19:22:46.908267+00:00", "level": "INFO", "logger": "fastmcp.server.auth.middleware",
 "msg": "Auth error returned: invalid_token (status=401)",
 "request_id": "evidence-denied-1", "trace_id": "59765b407b7e5cf26ca73e1a6f56a31e", "span_id": "d6a726686b915316"}
{"otel": "span", "name": "http.request", "trace_id": "59765b407b7e5cf26ca73e1a6f56a31e",
 "span_id": "d6a726686b915316", "attributes": {"http.request.method": "POST", "http.route": "/mcp",
 "gateway.request_id": "evidence-denied-1", "http.response.status_code": 401}}
```

The 172.18.0.2 address is Caddy inside the compose network; the client
address never reaches the application. The response carried a
`WWW-Authenticate` header (checked by `smoke.sh`). Metrics:
`gateway.http.requests{method=POST, route=/mcp, status_class=4xx}` incremented.
A denied *tool* call by an allowlisted viewer would instead appear as an
`mcp.tool` span with `gateway.outcome=denied` and a `gateway.authz.denials`
increment; that path is exercised by the unit tests and the mutation control,
not live, because it needs a second GitHub account.

## 3. One failed release: the fault-injected candidate

The release log on the host (`/opt/mcp-gateway/releases.log`), one JSON line
per action:

```
{"action": "deploy",   "at": "2026-09-13T18:55:05Z", "release_id": "sha-f61c8aca5973-drill",
 "image": "ghcr.io/neal-systems/mcp-gateway@sha256:a66e9f1d...", "outcome": "not_ready",
 "seconds_to_ready": null, "interruption_seconds": null}
{"action": "rollback", "at": "2026-09-13T18:55:10Z", "release_id": "sha-f61c8aca5973",
 "image": "ghcr.io/neal-systems/mcp-gateway@sha256:a66e9f1d...", "outcome": "ready",
 "seconds_to_ready": 3.532, "interruption_seconds": 3.534}
```

The same event seen from the three other vantage points:

- GitHub Actions run 34776025306 (`deploy.yml`, action `drill-not-ready`):
  the remote command returned exit 3, which the workflow treats as the
  expected outcome for a drill, and its summary carries the redacted remote
  output ("release sha-f61c8aca5973-drill did not become ready within 60s",
  "rolling back to known-good release sha-f61c8aca5973").
- The external probe (`interruption_probe.sh`, 0.5 s samples for 300 s):
  599 samples, 121 non-200, longest gap 61.11 s. That is the readiness
  timeout: the candidate served 503 on `/readyz` for the whole window
  because `GATEWAY_FAULT_INJECT=not_ready` was set on it alone.
- In the candidate's own telemetry, `/readyz` reported
  `"fault_injected": true` and `gateway.ready` was 0 until it was stopped.
  Those lines died with the candidate container and are not in the bundle;
  the same behaviour is captured locally in `tests/cloud/local_release_drill.sh`
  (check `drill.not_ready_exit3`).

For comparison, the healthy A-to-B upgrade an hour earlier logged
`interruption_seconds: 3.1` on the host and the external probe measured a
3.11 s gap, so the cost of a good release is about three seconds and the
cost of a bad one is the readiness timeout plus the rollback, about 65 s,
with the previous release serving again afterwards.

## What to take from this

- One `X-Request-ID` (or a generated one) ties access log, auth decision,
  spans and tool-call line together; one trace id does the same across spans.
- Denials are visible as their own outcome, not as absence of a call.
- A failed release is visible in four independent places that agree with
  each other: the host's release log, the workflow run, the external probe,
  and the manifest entry in `EVIDENCE.md`.
