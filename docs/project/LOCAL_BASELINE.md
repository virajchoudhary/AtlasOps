# AtlasOps Stage 0D local baseline

## Evidence identity

| Field | Value |
|---|---|
| Repository candidate tested | Stage 0D candidate tree on `chore/reproducible-dev-baseline`, based on `0fa2c086baefc0ec8909ecae91e84b498ae6746f` |
| Original upstream baseline | `bf9bd197c9f4a05ae55ade254802a9eef1a74356` |
| OS | Windows 11 |
| Architecture | x64 |
| Canonical Python | CPython 3.12.5 |
| Supported CI compatibility | Python 3.11 and Python 3.12 |
| Environment mechanism | Standard `venv` plus `pip` |
| Dependency input | `pyproject.toml` plus `requirements/dev.in` |
| Compiled lock | `requirements/dev-win-py312.lock` |
| Lock generator | `pip-tools==7.6.0` on Python 3.12.5 |
| Lock platform | Windows x64 / CPython 3.12 |
| Pinned third-party packages | 111 |

## Dependency declaration audit

Direct imports were inspected across `agents/`, `bench/`, `config/`, `training/`,
`scripts/`, `tests/`, `app.py`, `dashboard.py`, `eval.py`, `inference.py`, and
`leaderboard.py`. Standard-library and repository-local imports are omitted below.

| Imported package | Used by | Declared directly before Stage 0D | Classification | Previously supplied transitively | Stage 0D action |
|---|---|---:|---|---:|---|
| `fastapi` | `app.py`, coordinator, endpoint tests | No | runtime | Yes, through Gradio | Added directly to project dependencies |
| `uvicorn` | application/coordinator executable entry points | No | runtime | Yes, through Gradio | Added directly to project dependencies |
| `urllib3` | Argo CD tool | No | runtime | Yes, through Requests/Kubernetes | Added directly to project dependencies |
| `PyYAML` (`yaml`) | chaos-manifest tests | No | dev/test | Yes, through Gradio/Kubernetes | Added directly to `dev` |
| `matplotlib` | training-plot developer script | No | dev tooling | No in the Stage 0C environment | Added directly to `dev` |
| `httpx` | coordinator, agents, tests | Yes | runtime | N/A | No change |
| `pydantic` | application and coordinator models | Yes | runtime | N/A | No change |
| `requests` | integration tools and dashboard | Yes | runtime | N/A | No change |
| `jinja2` | communications templates | Yes | runtime | N/A | No change |
| `gradio` | dashboard | Yes | runtime | N/A | No change |
| `google.cloud.monitoring` | cloud-monitoring tool | Yes | runtime | N/A | No change |
| `pytest`, `pytest-asyncio` | tests | Yes | dev/test | N/A | No change |
| `torch`, `transformers`, `datasets`, `peft`, `trl` | inference/training modules | Yes | training/model | N/A | Kept in `train`; excluded from dev lock |
| `optimum` | guarded AMD optimization path | No | optional training/model | No | Deferred; code has an explicit fallback and training resolution is out of scope |
| `optuna` | optional GRPO hyperparameter search | No | optional training | No | Deferred; absence is explicitly handled |
| `flash_attn` | optional attention acceleration | No | optional training/GPU | No | Deferred; absence is explicitly handled |

The audit did not remove existing declarations merely because they lacked a static
direct import. Runtime integrations may use protocol clients or subprocesses, and
removal was not required for this reproducibility stage.

The build backend is pinned to `hatchling==1.32.0`, matching the compiled lock, so
isolated editable installs and package builds do not resolve an unbounded backend.

## Measured validation

| Check | Result |
|---|---|
| Clean environment path during measurement | `%TEMP%\atlasops-repro-20260811220819` |
| Install input | `requirements/dev-win-py312.lock` |
| Python | 3.12.5 |
| Installed package count | 113, including packaging tools and editable AtlasOps |
| `pip check` | PASS — no broken requirements |
| Compile | PASS |
| Ruff standard gate (`E9,F63,F7`) | PASS |
| Tests | PASS — 202 passed, 0 failed, 0 skipped |
| Warnings | 2 |
| Test duration | 4.60 seconds |
| Build | PASS — sdist and wheel |
| Safe imports | PASS — `config.runtime`, `agents.coordinator`, `bench.runner` |
| Clean-environment reproduction | PASS |
| External services contacted | NO, except package download during environment construction |
| Cloud credentials required | NO |
| Models downloaded | NO |
| Training executed | NO |
| Docker required | NO |

An initial harness attempt set `ATLASOPS_AUTO_HF_INFERENCE=0`, which intentionally
altered one environment-auto-detection test and produced 201 passes and 1 failure. The
override was removed, credential-like inherited variables were sanitized, and the full
suite then passed. This was a validation-harness correction, not a repository code fix.

## Known defect diagnostic

`python -m ruff check . --select F821` reports exactly one error:

- file: `bench/runner.py`
- line: 92
- issue: `tier` is passed to `judge_trajectory` before assignment
- status: known upstream baseline defect; not fixed during Stage 0D

The normal correctness gate remains `E9,F63,F7` until the defect is repaired in a
separately reviewed stabilization change.

## Environment-variable audit

No environment variable or secret is required for the local unit suite. The table
classifies variables that affect runtime or integration behavior; it does not contain
values from the host environment.

| Variable | Purpose | Required for local unit tests | Safe default | Secret | Required for real integration |
|---|---|---:|---|---:|---:|
| `ATLASOPS_AUDIT_SECRET` | Audit-record integrity | No; tests inject a placeholder | None; imports are safe and real execution fails closed | Yes | Yes for agent execution |
| `ATLASOPS_AUDIT_LOG` | Audit log path | No | Repository-local data path | No | No |
| `ATLASOPS_API_KEY` | Mutating API authentication | No | Unset development mode | Yes | Yes for protected deployment |
| `ALERTMANAGER_WEBHOOK_SECRET` | Webhook signature verification | No | Unset | Yes | Yes for signed webhooks |
| `LLM_API_KEY`, `JUDGE_API_KEY` | Agent and judge API authentication | No | Unset | Yes | Yes for authenticated remote models |
| `OPENAI_API_KEY`, `FIREWORKS_API_KEY` | Provider authentication | No | Unset | Yes | Only for those providers |
| `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN` | Hugging Face authentication | No | Unset | Yes | Only for authenticated HF use |
| `ARGOCD_PASS` | Argo CD authentication | No | None; explicit configuration required | Yes | Yes for Argo CD |
| `ARGOCD_USER` | Argo CD user | No | None; explicit configuration required | No | Yes for Argo CD |
| `DISCORD_WEBHOOK_URL`, `SLACK_WEBHOOK_URL` | Incident communications | No | Unset/disabled | Yes | Only for those integrations |
| `GCP_PROJECT` | Google Cloud project selection | No | Unset | No | Yes for GCP tools |
| `PROMETHEUS_URL`, `JAEGER_URL`, `ALERTMANAGER_URL` | Observability endpoints | No | Code defaults or unset | No | Yes for those integrations |
| `ARGOCD_URL` | Argo CD endpoint | No | None; explicit HTTP or HTTPS URL required | No | Yes for Argo CD |
| `GRAFANA_URL`, `BOUTIQUE_URL`, `COORDINATOR_URL` | Service endpoints | No | Code defaults or unset | No | Yes for those integrations |
| `VLLM_BASE`, `JUDGE_URL`, `HF_INFERENCE_BASE` | Model/judge endpoints | No | Local/code defaults | No | Yes for model execution |
| `AGENT_MODEL`, `JUDGE_MODEL`, `BACKEND` | Model/backend selection | No | Code defaults | No | Yes for model execution |
| `ATLASOPS_USE_HF_INFERENCE`, `ATLASOPS_AUTO_HF_INFERENCE`, `ATLASOPS_LIVE_JUDGE` | Inference-routing feature flags | No | Code defaults | No | Only for those modes |
| `ATLASOPS_SKIP_KUBECTL_INJECT` | Suppress live chaos injection | No | Disabled | No | No; safety/development control |
| `ATLASOPS_PUBLIC_BASE_URL` | Public callback/base URL | No | Unset | No | Deployment-specific |
| `ATLASOPS_DISCORD_EVERY_RUN_PING`, `DISCORD_BOT_USERNAME` | Discord behavior/identity | No | Code defaults | No | Discord only |
| `APPROVAL_TIMEOUT_SECONDS` | Approval timeout | No | 300 seconds | No | No |
| `KUBECTL_PATH` | kubectl executable location | No | Code default | No | Kubernetes only |
| `TRAJECTORIES_DIR`, `POSTMORTEM_DIR` | Generated-output locations | No | Repository-local paths | No | No |
| `SPACE_AUTHOR_NAME`, `SPACE_REPO_NAME`, `SPACE_ID`, `SYSTEM` | Hugging Face Space detection | No | Unset | No | HF Space only |

No `.env.example` was created. The documentation table is sufficient for the unit
baseline, while an accurate integration example should follow the separate security
repair described below rather than normalize unsafe defaults.

## Storage and artifacts

| Item | Measured size |
|---|---:|
| Tracked repository content | 999,297 bytes (about 0.95 MiB) |
| `.git` metadata | 1,251,461 bytes (about 1.19 MiB) |
| Existing project `.venv` | 472,896,441 bytes (about 451 MiB) |
| Temporary clean environment | 527,033,945 bytes (about 503 MiB) |
| Development lock | 7,792 bytes |

No model checkpoints, training datasets, Docker images, or GKE resources were created.
Temporary lock-generation and validation environments are removed after evidence is
recorded; the existing project `.venv` is retained.

## Warnings and pre-existing findings

- The two warnings measured during Stage 0D were the Starlette `TestClient`/`httpx`
  deprecation warning and the former unset-audit-secret fallback warning. Stage 1A
  removes the insecure fallback and its warning.
- Stage 1A removes the inherited active Argo CD credential/configuration defaults and
  records the required owner rotation assessment in `SECURITY_REMEDIATION.md`.
- Optional training dependencies (`optimum`, `optuna`, and `flash_attn`) are not part of
  the canonical development lock and were not resolved or validated on Windows.
- Package build isolation downloads the pinned Hatchling backend; that network access
  is package installation, not an AtlasOps external-service integration.
