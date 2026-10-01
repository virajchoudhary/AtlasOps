# AtlasOps Review Entry Point

The university continuation of Harikishanth/AtlasOps is **NOT_CERTIFIED**.
The inherited live-GKE judge instructions were superseded: they bypassed the
current incident preflight and implied training and deployment results that this
continuation has not established. Their original wording remains in Git history.
The MIT license and upstream attribution are unchanged.

## Review Current Evidence

- Start with [README.md](README.md) for current scope and supported commands.
- Read the [Master Pipeline status](docs/project/MASTER_PIPELINE_STATUS.md)
  and [current gap matrix](docs/project/UPSTREAM_README_CURRENT_GAP_MATRIX.md).
- Inspect the [technical report](docs/AtlasOps_Technical_Report.md) and
  [submission inventory](artifacts/SUBMISSION_SUMMARY.md).
- Keep historical, mock, and prospective evidence separate from empirical proof.
  G4 is `NOT_PASSED`; G9 and G13 are `REOPENED`.

## Local Read-Only Demo

From a source checkout with the documented development dependencies installed:

```bash
python dashboard.py
```

The Gradio demo browses preserved evidence. Selecting a scenario injects no fault
and proves no incident resolution. This does not certify the separate coordinator
or FastAPI deployment.

## Local Software Checks

```bash
python -m pytest tests/test_app_endpoints.py tests/test_approval_fail_closed.py \
  tests/test_stage14_demo_safety.py tests/test_stage15_submission_package.py -q
```

These are local software checks, not a live infrastructure or training experiment.
Use the documented Stage 4/6/7/8/9/13 contracts for separately authorized work.
The old `eval.py`, `leaderboard.py`, and `make sft` / `make grpo` live shortcuts
are disabled. No review instruction authorizes provisioning, fault injection,
model downloads, training, or final-Test access.
