# Reproducing the AtlasOps local development baseline

This procedure reproduces the safe local unit/static baseline. It does not validate
live infrastructure, external integrations, model execution, or training.

## Prerequisites

- Windows 11 x64
- Git
- CPython 3.12 (Python 3.12.5 was measured for Stage 0D)
- GitHub access only when pushing branches or opening pull requests

Docker, GCP, GKE, Kubernetes, Helm, cloud credentials, and model-provider credentials
are not required for the local unit baseline. Python 3.12 is the team's preferred local
development version. The project remains `Python >=3.11`, and Python 3.11 remains a
CI-supported compatibility target.

## Clone

Clone the university fork as `origin`, then register the original project as
`upstream`:

```powershell
git clone https://github.com/virajchoudhary/AtlasOps.git
Set-Location AtlasOps
git remote add upstream https://github.com/Harikishanth/AtlasOps.git
git remote -v
```

The original frozen upstream baseline is
`bf9bd197c9f4a05ae55ade254802a9eef1a74356`. Do not push to `upstream`.

## Create environment

From the repository root:

```powershell
py -3.12 -m venv .venv
.venv\Scripts\Activate.ps1
python --version
```

The expected canonical version on the measured machine is `Python 3.12.5`.

## Install

Install only the tracked Windows/Python-3.12 development lock:

```powershell
python -m pip --isolated install --index-url https://pypi.org/simple -r requirements/dev-win-py312.lock
```

Training extras are deliberately excluded. Do not add `.[train]` to this command.

## Runtime security configuration

The local unit/static baseline requires no real integration secrets. Tests inject
obvious placeholders only at mocked boundaries.

Real Argo CD operations require explicit `ARGOCD_URL`, `ARGOCD_USER`, and
`ARGOCD_PASS`. `ARGOCD_VERIFY_TLS` is optional and defaults to `true`; setting it to
`false` is an explicit operator opt-out and does not trigger global warning
suppression. Missing or invalid Argo configuration fails before HTTP.

Real coordinator or agent execution requires a private `ATLASOPS_AUDIT_SECRET`.
Imports remain safe without it, but execution fails before model or tool activity.
`ATLASOPS_AUDIT_LOG` may optionally select the append-only log path.

## Validate

Run these commands from the repository root:

```powershell
python --version
python -m pip check
python -m compileall -q agents bench config training scripts app.py dashboard.py eval.py inference.py leaderboard.py
python -m ruff check . --select E9,F63,F7
python -m pytest tests/
python -m build
python -c "import config.runtime, agents.coordinator, bench.runner; print('safe imports passed')"
```

The safe import check does not start a server, execute remediation, or contact cloud
services.

## Expected baseline

The independently measured Stage 0D clean-environment result on Windows 11 x64 with
Python 3.12.5 is:

- `pip check`: no broken requirements
- compile: passed
- Ruff `E9,F63,F7`: passed
- tests: 202 passed, 0 failed, 0 skipped, 2 warnings in 4.60 seconds
- build: sdist and wheel built successfully
- safe imports: passed

The two historical Stage 0D warnings were a Starlette `TestClient`/`httpx` deprecation
warning and the former insecure audit-fallback warning. Stage 1A removes that fallback
and its warning. The test count and warning text may change after intentional project
changes; record actual results rather than copying this baseline blindly.

For diagnostic evidence only, this command is expected to fail at the frozen baseline:

```powershell
python -m ruff check . --select F821
```

It reports one undefined `tier` use at `bench/runner.py:92`. Do not weaken the standard
gate or silently fix that defect as part of environment setup.

## Known limitations

This local baseline does not prove any of the following:

- GKE deployment or cloud remediation
- Chaos Mesh behavior
- Prometheus integration
- Jaeger integration
- Argo CD behavior
- model inference or published benchmark reproduction
- SFT or GRPO correctness
- GPU, ROCm, or CUDA compatibility
- production security or deployment readiness

No real secrets are needed for the unit suite. Integration variables and their safety
classification are recorded in `docs/project/LOCAL_BASELINE.md`.
