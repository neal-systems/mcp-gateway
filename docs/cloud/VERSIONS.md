# CI/CD tool and action versions

Pins used by `.github/workflows/*.yml` and `scripts/`. Every `uses:` in the
workflows carries the commit SHA below with a `# vX.Y.Z` comment; each SHA
was verified to resolve on GitHub before use
(`gh api repos/<owner>/<repo>/commits/<sha> --jq .sha`).

| Tool / action | Version | Pin |
|---|---|---|
| actions/checkout | v7.0.1 | `3d3c42e5aac5ba805825da76410c181273ba90b1` |
| actions/setup-python | v7.0.0 | `5fda3b95a4ea91299a34e894583c3862153e4b97` |
| actions/upload-artifact | v7.0.1 | `043fb46d1a93c77aae656e7c1c64a875d1fc6a0a` |
| actions/download-artifact | v8.0.1 | `3e5f45b2cfb9172054b4087a40e8e0b5a5461e7c` |
| docker/login-action | v4.6.0 | `dbcb813823bdd20940b903addbd779551569679f` |
| docker/setup-buildx-action | v4.3.0 | `37fe631027851001ddb9b187196cc803df7f5f0e` |
| docker/build-push-action | v7.3.0 | `53b7df96c91f9c12dcc8a07bcb9ccacbed38856a` |
| docker/metadata-action | v6.2.0 | `dc802804100637a589fabce1cb79ff13a1411302` |
| aws-actions/configure-aws-credentials | v6.2.4 | `cbe3b392738ccf3f987d68400dafcf4b0624a56c` |
| hashicorp/setup-terraform | v4.0.1 | `dfe3c3f87815947d99a8997f908cb6525fc44e9e` |
| hadolint/hadolint-action | v3.5.0 | `06be81baf89a55ffd0e24b8f04a4185738dd3387` |

| CLI tool | Version | How it's invoked in CI |
|---|---|---|
| Terraform | 1.16.2 | `hashicorp/setup-terraform` `terraform_version:` input |
| actionlint | 1.7.12 | `docker run rhysd/actionlint:1.7.12` |
| shellcheck | 0.11.0 | `docker run koalaman/shellcheck:v0.11.0` |
| hadolint | 2.15.1 | bundled by `hadolint/hadolint-action@v3.5.0` |
| gitleaks | 8.30.1 | `docker run zricethezav/gitleaks:v8.30.1` (chosen over `gitleaks/gitleaks-action` to stay credential-free on a public repo without a `GITLEAKS_LICENSE`) |

Python: 3.12 (matches `app/Dockerfile`'s `python:3.12-slim` base and
`actions/setup-python`'s `python-version: "3.12"`).
