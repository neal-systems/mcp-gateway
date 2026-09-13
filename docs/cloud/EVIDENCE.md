PASS=7 FAIL=0 BLOCKED=0 NOT_RUN=0

| id | class | status | environment | finished_at | duration_s | git_sha | image_digest | evidence path | notes |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| container.log_redaction | container | PASS | local | 2026-09-13T12:36:16Z | 7.39 | a04853d0a3a7 | sha256:2ffe3bfc640d | evidence/raw/container.log_redaction-20260913T123609Z.log |  |
| container.restart_persistence | container | PASS | local | 2026-09-13T12:36:09Z | 7.05 | a04853d0a3a7 | sha256:2ffe3bfc640d | evidence/raw/container.restart_persistence-20260913T123600Z.log |  |
| container.smoke | container | PASS | local | 2026-09-13T12:36:00Z | 3.62 | a04853d0a3a7 | sha256:2ffe3bfc640d | evidence/raw/container.smoke-20260913T123556Z.log |  |
| unit.negative_control_authz | unit | PASS | local | 2026-09-13T12:35:38Z | 31.99 | a04853d0a3a7 | - | evidence/raw/unit.negative_control_authz-20260913T123504Z.log | scope.is_tool_allowed mutated to always True in a scratch copy |
| unit.redaction_sentinels | unit | PASS | local | 2026-09-13T12:35:55Z | 16.79 | a04853d0a3a7 | - | evidence/raw/unit.redaction_sentinels-20260913T123538Z.log |  |
| unit.signing_key_regression | unit | PASS | local | 2026-09-13T12:35:04Z | 20.49 | a04853d0a3a7 | - | evidence/raw/unit.signing_key_regression-20260913T123442Z.log |  |
| unit.suite | unit | PASS | local | 2026-09-13T12:34:42Z | 23.44 | a04853d0a3a7 | - | evidence/raw/unit.suite-20260913T123417Z.log |  |

Raw logs under evidence/raw/ are private and not published.
