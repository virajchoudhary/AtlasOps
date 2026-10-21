# G9 local offline progress record — 21 October 2026

**Scope:** model-free and tiny-random-fixture tests only. No pretrained weights
were downloaded or loaded, no project training ran, nothing was pushed.
G9 remains **NOT_PASSED / FROZEN**; scientific status remains `NOT_CERTIFIED`.
These results are software evidence, not learning evidence, and not independent review.

## Source and environments

- Source: `origin/main` `b23d6cb4319b77b4d8606934e401141bcb01d23f` (this branch adds documentation only).
- Host: Apple M4 (arm64), 16 GiB unified memory, no CUDA, macOS (Darwin 24.6.0).
- Dev venv: Python 3.12.11, `pip install -e ".[dev]"` (no Torch/TRL).
- Pinned venv, kept **outside** the checkout and separate from the dev venv:
  Python 3.12.11, `torch==2.7.1` (macOS arm64 PyPI wheel; CI uses the Linux `+cpu`
  wheel), `trl==0.19.1`, `transformers==4.57.6`, `peft==0.17.1`,
  `accelerate==1.14.0`, `datasets==4.8.5`, `tokenizers==0.22.2`,
  `huggingface-hub==0.36.2` — the exact versions in the CI `trainer` job.
  Run with `HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 HF_DATASETS_OFFLINE=1
  CUDA_VISIBLE_DEVICES="" ATLASOPS_REQUIRE_TRL_INTEGRATION=1
  ATLASOPS_REQUIRE_PINNED_CPU_STACK=1`.

## Results

| Command (from the G9 guide / CI trainer job) | Dev venv | Pinned venv |
| --- | --- | --- |
| `pytest tests/test_grpo_reward_mechanics.py tests/test_grpo_controlled.py` | 29 passed, 20 skipped (no Torch/tokenizers) | 25 passed + 24 passed, 0 skipped |
| `pytest -rs tests/test_grpo_observation_installed_trl.py` | 1 passed, 8 skipped (pinned stack absent) | **9 passed, 0 skipped** |
| `pytest tests/test_grpo_reload_installed.py` | — | **1 failed by design**: `Verified-offline profile requires Linux x86_64 seccomp` |
| `pytest tests/test_sft_chunked_loss.py tests/test_sft_t4_attention.py tests/test_sft_supervised_trainer.py` | — | 11 passed |
| `pytest tests/test_base_sft_validation.py tests/test_verify_base_sft_validation.py` | — | 29 passed |
| `ruff check training/grpo_reward.py training/grpo_controlled.py scripts/accept_grpo_controlled.py --select E9,F63,F7,F821,F401,F841` | All checks passed | — |

What is new relative to the [G9 safeguards guide](../G9_OFFLINE_SAFEGUARDS_HANDOFF.md):
the Torch numerical probe and all installed-trainer cases (including the
`equal-reward-kl-only` counterexample) **executed** here on a second platform
outside CI, rather than skipping. This covers the beta-zero environmental-policy
gradient hook, completion/advantage pairing and the claim-/syntax-/KL-/penalty-only
and first-group-rescue rejections, on tiny random fixtures only.

The fresh-reload socket guard could not be exercised: `training/grpo_reload_isolation.py`
deliberately refuses non-Linux-x86_64 hosts. That is correct fail-closed behavior,
not a defect, but it means reload isolation still has only Linux CI evidence.

## Resource qualification

This host does **not** qualify for the prospective Train-only pilot: there is no
CUDA device, the pinned stack is CPU-only here, and 16 GiB of unified memory is
below the proposal's 24 GiB VRAM allowance. No MPS or reduced-precision
substitute was attempted, since that would silently change the protocol.

## Status against the PR checklist

- Exact-source pinned offline test and lineage evidence: **partially satisfied**
  (all pinned trainer/mechanics cases pass on macOS arm64; reload isolation is
  platform-blocked here). Left unticked.
- Training-resource/model qualification, Train-only v2 permission, actual
  training evidence, accepted gradient/update/checkpoint: **not started** —
  each requires project-lead reopening and new resource authorization.
