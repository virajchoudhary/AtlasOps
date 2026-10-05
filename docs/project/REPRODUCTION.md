# Reproducing the AtlasOps local development baseline

Current source/evidence basis: `7ac0cfb5c5fbd77d82500a72fd06b802c2b826b7`,
5 October 2026. The commands below validate software without model
execution or live infrastructure. The Stage 0D figures are historical.
Current results and package hashes are in the
[evidence index](../EVIDENCE_INDEX.md) and
[submission summary](../../artifacts/SUBMISSION_SUMMARY.md).

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
python -m ruff check . --select E9,F63,F7,F821
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

The original Stage 0D `F821` failure at `bench/runner.py:92` is historical.
G2 repaired the tier-ordering defect. The current CI correctness gate
includes F821 and must pass:

```powershell
python -m ruff check . --select F821
```

Do not weaken the current correctness gate to match the old negative baseline.

## Current Safe Review Path

```powershell
python -m pytest tests/test_stage14_demo_safety.py tests/test_stage15_submission_package.py tests/test_current_project_truth.py
python -m demo.launcher --host 127.0.0.1 --port 7860
```

The demo reads preserved evidence and requires no cluster, model endpoint or
GPU. Optional installed-trainer tests use tiny random local fixtures, never
pretrained weights or real project training. Offline environment variables
`HF_HUB_OFFLINE=1`, `TRANSFORMERS_OFFLINE=1`, `HF_DATASETS_OFFLINE=1`
and `CUDA_VISIBLE_DEVICES=""` keep optional test execution bounded.
Full pytest includes mocked partition-isolation checks. Those fixtures are
not access to empirical final-Test or Leaderboard outcomes.

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
