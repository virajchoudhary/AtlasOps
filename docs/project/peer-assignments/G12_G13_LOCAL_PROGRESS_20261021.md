# G12/G13 local offline progress record — 21 October 2026

**Scope:** model-free G13 safeguard reproduction only. No services, weights,
training, faults, remediation, held-out data or deployment were used. Nothing
was pushed. G12 remains **IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**; G13 remains
**REOPENED / NOT_CERTIFIED**. G10/G11 stay OUT_OF_SCOPE. This is not independent review.

## Reproduction on source `b23d6cb4319b77b4d8606934e401141bcb01d23f`

Host: Apple M4 (arm64), macOS (Darwin 24.6.0), Python 3.12.11 dev venv from
`pip install -e ".[dev]"`. Commands from the [G13 safeguards guide](../G13_OFFLINE_SAFEGUARDS_HANDOFF.md):

| Command | Result |
| --- | --- |
| `pytest tests/test_candidate_measurement.py tests/test_candidate_replay.py tests/test_candidate_lineage.py tests/test_candidate_adapters.py -q` | 274 passed |
| `ruff check bench/candidate_measurement.py bench/candidate_replay.py bench/candidate_lineage.py --select E9,F63,F7,F821,F401,F841` | All checks passed |

These are software results on synthetic fixtures. They do not contain incident
observations, a third arm, or any TTR value; all TTR/censor durations remain null.

## Why the remaining offline item was not implemented

The guide's next software step is a contract-bound clock-attestation and
independent-recomputation path. Its inputs — clock identities, precision,
offsets, skew bounds, alert-origin and censoring rules, invalidation adjudication,
population, seeds, arm order, null rules and comparison formulas — must first be
frozen by the project lead. Implementing it against an invented contract would
pre-empt that decision, so it is left for after the freeze.

## Dependency status

- Accepted G9 SFT+GRPO checkpoint: **absent** (G9 NOT_PASSED/FROZEN; see the G9 branch record).
- Integrated G12 run permit, measurement freeze, held-out access, reviewer: **absent**.

## Status against the PR checklist

None satisfied. The first box needs independent acceptance, not this reproduction.

## Prospective three-arm contract

See the [three-arm freeze sheet candidate](G12_G13_THREE_ARM_FREEZE_CANDIDATE_20261021.md):
current source/scorer hashes, the proposal's denominator/recovery/TTR/null rules, and every
owner field left `UNSPECIFIED`. Arm 3 (accepted GRPO) does not exist and is not substituted.

## Record metadata

- Branch `handoff/g12-g13-integrated-measurement-20261020`; remote base `366ddab`; source `main` `b23d6cb`.
- Gate status before and after: G12 **IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**; G13 **REOPENED / NOT_CERTIFIED** (unchanged).
- Files: this record; the freeze sheet candidate; the brief (one "Local progress" line). No code changed.
- No held-out (Final-Test/adversarial) membership or outcomes were read.
- Final SHAs, full test counts and skip reasons: `~/Documents/AtlasOps-review/local-closure-20261021/README.md` (local only).
- **No remote push occurred. No empirical result was fabricated.**
