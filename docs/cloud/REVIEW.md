# Adversarial review and resolutions

One fresh-context, read-only review of commit cfbd37d by a reviewer that
implemented none of the code (a Codex Sol session, high reasoning effort).
It executed pytest for the scope and telemetry suites, gitleaks over the
branch history, terraform fmt, and bash syntax checks; Docker, the release
drill and terraform validate were blocked by its read-only sandbox and are
covered by the lead's own runs recorded in EVIDENCE.md. Verdict was
FIX-THEN-SHIP with no CRITICAL findings. Every finding and its resolution:

| # | Severity | Finding | Resolution (commit) |
|---|---|---|---|
| 1 | HIGH | deploy.yml interpolated workflow inputs inside run blocks; a crafted release_id executed before validation and after OIDC credentials existed | Inputs reach the shell only through env, the action is validated, the summary passes through scripts/cloud/ops/redact.py (ae06418) |
| 2 | HIGH | Trust subject used the legacy name-only form; GitHub issues the immutable owner-id/repo-id form for repositories created after 2026-07-15 (this one: 2026-09-03), so the role could never be assumed | Verified against GitHub's OIDC reference; trust now uses repo:neal-systems@324300420/mcp-gateway@1355437034:environment:demo with a legacy switch; the workflow prints the live subject before assuming (ae06418) |
| 3 | HIGH | Formatted exception text bypassed log redaction; evidence second pass missed key=value shapes; raw SSM output reached the public job summary | exc_info and stack text redacted in both formatters (7a1c1e7); evidence pass covers key=value and JWT shapes; summary redacted (ae06418) |
| 4 | HIGH | A failed candidate under the known-good release id overwrote its own fallback and could not roll back | Same-id different-image deploys are refused (exit 2); drill candidates get <id>-drill; both covered by the local drill (ae06418) |
| 5 | HIGH | First boot raced the Elastic IP association (no internet for dnf) and the volume attachment | Launch-time public address, ten-minute wait for the state device, package install retries (ae06418) |
| 6 | MEDIUM | Docker could restart the app before the state volume mounted (nofail) and write state to the root disk | docker.service drop-in RequiresMountsFor=/srv/mcp-gateway; a missing mount stops Docker (ae06418) |
| 7 | MEDIUM | ssm:ListCommandInvocations on * exposed unrelated command output; unused | Removed from the deploy role (ae06418) |
| 8 | MEDIUM | Denied tool names became metric labels: unbounded cardinality | Labels use the real name only for catalogued tools, else "other" (7a1c1e7) |
| 9 | MEDIUM | Evidence manifest recorded a git sha whose tree did not contain the evidence scripts (run on an uncommitted tree) | Every entry now records git_dirty; the final evidence run is executed on the committed head (ae06418, see EVIDENCE.md) |
| 10 | LOW | gateway.ready gauge used all(checks) and so read 0 during healthy operation | One readiness decision shared by /readyz and the gauge (7a1c1e7) |

Categories the reviewer reported clean: authorization fail-open, health
endpoint exposure, secret flow through Terraform, digest validation, action
pinning and PR credential isolation, tool-argument and request-id handling.

What the review could not do: run against AWS (no credentials exist on the
host, by design at this stage), so the OIDC subject fix is verified against
documentation and the repository's creation date, not against a live token.
The deploy workflow's first run will print the live subject.
