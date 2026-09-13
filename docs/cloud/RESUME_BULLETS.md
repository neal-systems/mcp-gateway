* Deployed a read-only MCP service to AWS using GitHub Actions OIDC and SSM Run Command to execute digest-pinned container releases with automated health-gated rollback on readiness failure.
  Evidence: cloud.oidc_exchange, cloud.release_a, cloud.upgrade_a_to_b (3.11 s measured gap), cloud.failed_candidate_rollback, cloud.rollback_action, cloud.restart_and_reboot, cloud.teardown; oauth.live_login_authorized

* Containerized the FastMCP gateway with Docker Compose, enforcing unprivileged execution, state-volume signing key persistence across restarts, and isolated HTTP liveness and readiness probes.
  Evidence: container.smoke, container.restart_persistence, unit.signing_key_regression

* Instrumented gateway telemetry using an OpenTelemetry stdout exporter with parent-based trace sampling and structured JSON logging with regex redaction validated against synthetic sentinel secrets.
  Evidence: unit.redaction_sentinels, container.log_redaction
