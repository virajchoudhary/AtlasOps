# G6/G8 local offline progress record — 21 October 2026

**Scope:** offline resolution of the A6/B protocol inputs from source, plus
model-free test reproduction. No inference, workload, fault, remediation,
Final-Test or Leaderboard access occurred. Nothing was pushed.
G6 and G8 remain **IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**. The diagnostic
F1 values (Base 0.16875, SFT-v17 0.15935, delta −0.00940) stay unchanged negative
diagnosis evidence. This record is not independent review and approves nothing.

## Hard dependency status

Unmet: accepted causal G4 evidence (see the G4 branch: offline NO-GO on
21 Oct 2026 — no container engine/cluster, source mismatch, no qualified model
or operator path). Neither A6 nor B can be requested before that.

## Protocol inputs resolved from source `b23d6cb4319b77b4d8606934e401141bcb01d23f`

`config.scenario_catalog.verify_split_disjointness()` returns `is_valid`,
`is_disjoint` and `is_exhaustive` all true (16 Train / 6 Validation / 6 Test, 28 static).
`VAL_SPLIT` in declared order, with LF-normalized manifest SHA-256
(all six files are committed with CRLF line endings):

| # | Scenario | Manifest | LF-normalized SHA-256 |
| --- | --- | --- | --- |
| 1 | `single_fault/sf-006` | `bench/chaos_manifests/single_fault/sf-006.yaml` | `85739fb0a76713ef248724c975b75a526bddd531a3933c28bf434acee7d94bac` |
| 2 | `single_fault/sf-007` | `bench/chaos_manifests/single_fault/sf-007.yaml` | `574f20851039f8b0b3f0bacd39d8b6dd1161dd41a9e0dcd41e972b1acc1c1a6a` |
| 3 | `cascade/cs-004` | `bench/chaos_manifests/cascade/cs-004.yaml` | `b9e32cec2c6107155dcb8b456d1907bbe475e57fc111b6abd429289ce20809df` |
| 4 | `multi_fault/mf-004` | `bench/chaos_manifests/multi_fault/mf-004.yaml` | `859f1595b0b01ba863b868fdbe028bcf874958ecc96fde8896b730f40808848b` |
| 5 | `named_replays/hist-fastly-2021` | `bench/chaos_manifests/named_replays/hist-fastly-2021.yaml` | `79e5c1d716c66cefbb5592190e67fabe709dc73cf0664967e8e118b545a21c3e` |
| 6 | `named_replays/hist-facebook-bgp-2021` | `bench/chaos_manifests/named_replays/hist-facebook-bgp-2021.yaml` | `7c7053d713789c333649b09aca1a64c74f62795672a0c0f40e4ba14fb599a59f` |

These hashes must be re-verified at whatever commit is eventually frozen.

### A6 candidate — G6 Base-only (separately approvable)

Six fresh episodes, scenarios 1–6 in the order above; pinned Base only,
adapters disabled; seed 1337, greedy, temperature 0, top_p 1, 512 new tokens;
3,600 s per episode (21,600 s total); at most one approved remediation action per
episode; run-owned cleanup before advancing; no retry or replacement. Claim is a
descriptive Validation incident baseline only.

### B candidate — G8 matched Base/v17 (separately approvable)

Twelve distinct arms (never reusing A6 episodes), predeclared before outcomes:

| Pair | Scenario | First arm | Second arm |
| --- | --- | --- | --- |
| 1 | `single_fault/sf-006` | Base | v17 |
| 2 | `single_fault/sf-007` | v17 | Base |
| 3 | `cascade/cs-004` | Base | v17 |
| 4 | `multi_fault/mf-004` | v17 | Base |
| 5 | `named_replays/hist-fastly-2021` | Base | v17 |
| 6 | `named_replays/hist-facebook-bgp-2021` | v17 | Base |

Same Base/tokenizer; v17 adapter enabled only in its arm; identical sampling,
role prompts, tools, predicates and limits; 3,600 s per arm (43,200 s total);
recorded pre-arm reset/telemetry state for every arm; every failed arm retained.
The single-action cap may fail cascade/multi-fault pairs; those stay negative.

### Fields that cannot be frozen offline (owner-supplied)

Base/tokenizer/adapter content digests bound to the served identity; exact
frozen source commit; Alertmanager receiver and clock identities with skew bounds;
operator identity and action-approval expiry; cleanup deadline; G6's evaluation-
population rule; the predeclared G8 improvement adjudication. These belong to the
project lead, host owner and operator.

## Existing diagnostic reproduced (scope: alert-only diagnosis text, not incidents)

`python -B -m scripts.verify_base_sft_validation --run artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1 --output <new path>`
recomputed the 3 October 2026 Kaggle T4 run from its raw responses with the
repository's independent standard-library scorer:

| Metric | Base | SFT-v17 | SFT − Base |
| --- | ---: | ---: | ---: |
| Scheduled / scored | 6 / 6 | 6 / 6 | — |
| Schema conformance | 1.0 | 1.0 | 0.0 |
| Mean token-set diagnostic F1 (4 dp) | 0.1687 | 0.1593 | −0.0094 |
| `resolution_rate` / `avg_time_to_resolve_s` | null | null | — |

The output is equal, as parsed JSON, to the committed
`artifacts/evidence/stage8/base-sft-validation-v1/local-independent-recompute-v1.json`.
Its `status: PASS` means the recomputation matched; it is **not** a G6 or G8 pass.
This is descriptive diagnosis evidence with no improvement, and no evidence about
live incident resolution.

## Model-free test reproduction

The full repository suite run for this record is reported in the G14/G15 local
progress record on its branch. Relevant focused suites on the same source:
G13 measurement/replay/lineage/adapters 274 passed (null, negative and blocked
rows preserved), Base/SFT validation contract 29 passed.

## Status against the PR checklist

None satisfied. "Synthetic offline evaluators preserve negative/null outcomes" is
covered by existing passing tests but still needs independent review to tick.

## Offline correctness review scope

Measurement, replay, lineage and adapter safeguards (null/negative/blocked rows,
unknown lifecycle names, `telemetry_loss` pending adjudication, pre-cleanup endpoints,
denominator preservation) are shared with G13 and were exercised by the 274
model-free tests; no new correctness gap was found, so no code or test was added.
A dedicated six-episode incident executor and its contract-bound clock
attestation do not exist yet; they depend on owner-frozen fields listed above.

## Record metadata

- Branch `handoff/g6-g8-incident-evaluation-20261020`; remote base `7e46a2c`; source `main` `b23d6cb`.
- Gate status before and after: **IMPLEMENTED / EMPIRICAL EVIDENCE MISSING** (unchanged).
- Files: this record (added); the brief (one "Local progress" line added).
- Final SHAs, full test counts and skip reasons: `~/Documents/AtlasOps-review/local-closure-20261021/README.md` (local only).
- **No remote push occurred. No empirical result was fabricated.**
