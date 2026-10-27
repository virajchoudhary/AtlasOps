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

## Defect fixed: racy subprocess handoff in the G9 approval-channel test

The full suite on `b23d6cb` had one failure:
`tests/test_g9_approval_channel.py::test_operator_decision_crosses_process_and_restart_invalidates_token`
raised `JSONDecodeError: Expecting value` while reading `result.json`. The helper
`tests/g9_approval_process.py` wrote `ready.json`/`result.json` with
`Path.write_text`, which creates the file before filling it; the parent polls
`is_file()` and could read it empty. The Stage 4 helper already avoids this with
an atomic `publish_json`. This branch copies that pattern into the G9 helper;
no assertion, timeout or product code changed.

| Source | Isolated runs of that test | Passed | Failed |
| --- | --- | --- | --- |
| `b23d6cb` (before) | 20 | 7 | 13 |
| this branch (after) | 20 | 20 | 0 |

`tests/test_g9_approval_channel.py`, `tests/test_stage15_submission_package.py`
and `tests/test_repository_hygiene.py` then pass together (97 passed, 1 skipped),
and CI's Ruff rule set passes. This race raises an error rather than hanging,
so it does not explain the observation-window stall the safeguards guide records;
that stall's cause is still not established.

## Resource qualification

This host does **not** qualify for the prospective Train-only pilot: there is no
CUDA device, the pinned stack is CPU-only here, and 16 GiB of unified memory is
below the proposal's 24 GiB VRAM allowance. No MPS or reduced-precision
substitute was attempted, since that would silently change the protocol.

## Status against the PR checklist

- Exact-source pinned offline test and lineage evidence: **partially satisfied**
  (all pinned trainer/mechanics cases pass on macOS arm64; reload isolation is
  platform-blocked here). Left unticked.
- Offline defect fixed: G9 approval-channel helper race (see above).
- Training-resource/model qualification, Train-only v2 permission, actual
  training evidence, accepted gradient/update/checkpoint: **not started** —
  each requires project-lead reopening and new resource authorization.

## Record metadata

- Branch `handoff/g9-grpo-policy-20261020`; remote base `9c092da`; source `main` `b23d6cb`.
- Gate status before and after: **NOT_PASSED / FROZEN** (unchanged).
- Files: `tests/g9_approval_process.py` (atomic handoff writes; test helper only); this
  record; [Train-only v2 freeze sheet candidate](G9_TRAIN_ONLY_V2_FREEZE_CANDIDATE_20261021.md);
  the brief (one "Local progress" line).
- Final SHAs, full test counts and skip reasons: `~/Documents/AtlasOps-review/local-closure-20261021/README.md` (local only).
- **No remote push occurred. No empirical result was fabricated. No checkpoint was promoted.**

## Addendum — 22 October 2026: open review findings and a Linux pinned run

This record was later pushed with the rest of the local closure work in
`chore/local-closure-20261022`; the statements above describe the state when
it was written. G9 remains **NOT_PASSED / FROZEN**. Nothing below is learning
evidence, a training run or a checkpoint promotion.

### Codex review findings on the export and reload path

Sixteen automated review threads on PRs 180–184 were never answered. Ten were
already fixed (seven in this closure branch, three earlier on `main`). The six
below were still present in the code and are now fixed with regression tests
that fail on the unfixed source and pass after:

| Finding | Fix | Regression tests |
| --- | --- | --- |
| PR 180 (P1): acceptance could be issued anywhere outside the run folder; `local_evidence_destination` was only checked for non-empty text | `scripts/accept_grpo_controlled.py`: the run, reload report and acceptance report must resolve inside the receipt's absolute, existing local evidence store, and the accepted run may not be the training `output_path` itself | `tests/test_grpo_controlled.py::test_export_requires_the_admitted_local_evidence_store`, `…rejects_paths_outside_the_local_evidence_store`, `…rejects_acceptance_of_the_training_output_itself` |
| PR 180 (P2): observed package versions and GPU details were checked, then discarded | `training/grpo_controlled.py`: `verify_controlled_runtime()` returns the observed snapshot, stored as `receipt["verified_runtime"]` in the run manifest | `test_admitted_runtime_snapshot_records_observed_versions_and_gpu` |
| PR 181 (P1): reload compared only `HEAD`, so a modified worktree could be attributed to the clean commit | `scripts/reload_grpo_controlled.py`: `require_clean_reload_source()` requires the receipt commit and a clean tree | `test_reload_refuses_a_modified_or_different_source_tree` |
| PR 181 (P2): a guard-installation failure lost the collected probes | `training/grpo_reload_isolation.py`: the failure stage and all collected evidence are attached to the raised error, which the reload report already preserves | `tests/test_grpo_reload_isolation.py::test_guard_installation_failure_preserves_isolation_evidence` |
| PR 182 (P2): discovery inventories zero-byte cache files that acceptance then refused | `validate_gpu_discovery()` accepts non-negative sizes | `test_gpu_discovery_accepts_zero_byte_runtime_files` |
| PR 182 (P2): the CUDA listener name was checked only by prefix and suffix | `seal_gpu_runtime()` records `pid_namespace_inode`; the validator requires the complete `@cuda-uvmfd-<inode>-<pid>@` name | `test_gpu_discovery_requires_the_exact_namespace_bound_listener`, `…rejects_listener_without_recorded_namespace` |

Remaining limitation: the receipt does not name the local host, so acceptance
proves a supervised copy inside the admitted store, not the host's identity.
`docs/project/CONTROLLED_G9_ADMISSION_V1.md` is hash-bound by
`admit_execution()` and was deliberately not edited.

### Pinned trainer tests on Linux x86_64

Run on a Linux x86_64 cloud container with Python 3.12, `torch==2.7.1` (the
PyPI build, run on CPU with `CUDA_VISIBLE_DEVICES=""`; the PyTorch CPU wheel
index was unreachable from that network), `trl==0.19.1`, `transformers==4.57.6`,
`peft==0.17.1`, `accelerate==1.14.0`, `datasets==4.8.5`, `tokenizers==0.22.2`,
`huggingface-hub==0.36.2`, with the CI trainer job's offline variables:

| Command | Result |
| --- | --- |
| `pytest tests/test_grpo_observation_installed_trl.py` | 9 passed |
| `pytest tests/test_grpo_reward_mechanics.py -rs` | 25 passed, 0 skipped |
| `pytest tests/test_grpo_reload_installed.py` | **1 passed** (fresh local reload under the permanent socket guard; refused by design on macOS above) |
| `pytest tests/test_sft_chunked_loss.py tests/test_sft_t4_attention.py tests/test_sft_supervised_trainer.py` | 11 passed |
| `pytest tests/test_base_sft_validation.py tests/test_verify_base_sft_validation.py` | 29 passed |

The checklist box "Exact-source pinned offline test and lineage evidence" stays
unticked: these runs are not independent review of a final merged commit.
