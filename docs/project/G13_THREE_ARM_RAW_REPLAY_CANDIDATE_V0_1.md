# G13 three-arm raw replay candidate v0.1

**Status: PROSPECTIVE SOFTWARE CANDIDATE / NOT FROZEN / NON-EMPIRICAL.**
This is a review input for the GAI + RL scope amendment, not an approved
scientific protocol or authority to read final Test, train, load weights,
request P1 approval, inject faults, spend on compute, or deploy. G13 remains
`REOPENED`; the G15 gate is `PARTIAL` and the submission package remains
`NOT_CERTIFIED`. The v0.1-v0.3 protocol records,
historical five-arm output, and frozen evidence are unchanged.

## Candidate measurement path

`bench.candidate_replay.replay_candidate_comparison` accepts exactly
`Zero-Shot Baseline`, `SFT Model`, and `SFT + GRPO` in that order. It checks
ordered scenario membership, cross-arm base/tokenizer and V2-to-V4 adapter
lineage, matched evaluation settings, caller-supplied source pins, and the
runtime scorer/configuration hashes before computing anything. The native
G6/G8 adapters retain diagnosis rows and failures without inventing an
authorized fault, delivered alert, action, verifier, reward, or TTR. G9
events retain their raw references, failures, and structural run outcome.
Caller-declared identities remain separate from identities actually bound
to raw records. A matching declaration is not an immutable serving
attestation or independent source authentication.

A separate prospective common-episode JSONL input can carry all required
events for synthetic contract tests. It is bounded and hash checked, but
its per-arm source identity and digest are still caller supplied. The
replay performs no repository or partition fetch; it cannot authenticate
the origin or authorization of unlabeled bytes passed to it. Explicit
Test-like partition metadata is rejected as an additional software guard,
not a grant of access to any partition.

The candidate A1 result uses the first conclusive verifier recovery after
an executed action and before harness cleanup. Explicit model or
no-execution failures remain eligible negatives; missing observations
stay null, infrastructure failures await independent adjudication, and
unscorable terminals remain null under a future reviewed rule. A2 is a
separate per-episode candidate:
`0.75 * verified_recovery + 0.25 * required_check_coverage -
0.25 * false_resolution_claim`, without clipping or replacement of
unknown components by zero. It is not the existing G9 step-average
training diagnostic. TTR uses delivered alert to first pre-cleanup
conclusive success; unresolved episodes are right-censored and not
assigned a fabricated duration.

The replay reports observed and scheduled populations, pre-start
ineligibility, eligible negatives, missing slots, pending adjudication,
null reasons, and raw-derived candidate resolution, reward, diagnosis
accuracy, and observed-resolved TTR only when inputs support them.
Declared summaries are compared where a raw recomputation exists and
otherwise labeled `NOT_COMPUTED` or `NOT_COMPARABLE`; they never supply a
missing metric. Format/Comms validity, safety-violation rates,
diagnosis macro-F1/confusion, censor durations and survival estimates,
independent-run intervals, and adjudicated exclusions are **not**
established by this candidate.

## Byte anchors

The following hashes identify the local candidate at software commit
`587f9ad4346d0e8e201fb67aad70eb5f2cea5c45`. They are SHA-256 of raw Python
source bytes, not model, checkpoint, evaluator-run, or evidence hashes.
`comparison_scorer_sha256` is SHA-256 of canonical JSON for the ordered
five-entry runtime source manifest (`path`, `sha256`), using sorted keys,
compact separators and UTF-8:

| Source | SHA-256 |
| :--- | :--- |
| `bench/candidate_adapters.py` | `903f790a14537bfcd4c7e19b2b3f579674d487a9af43964d53956a2a4e8f5e2b` |
| `bench/candidate_lineage.py` | `396ff6d389f2b83631d8b5b50c6a08c74f9df42317dbd48a9d6a2825eb956aa9` |
| `bench/candidate_measurement.py` | `8b5448ea354652c6e45024a463f53702919e94908c92a52995932d08cff49552` |
| `bench/candidate_replay.py` | `8e48a1b8900acf071596c932bf524a6855b187ad14249d9dc3d295b739a70daa` |
| `bench/episode_membership.py` | `c598ab421638db9cf94c2f827c39e2023cc81eb47e413160db68289e853015c8` |

`comparison_scorer_sha256`:
`3a985d697a5a4c314bd30be258160da633b9ca3ac67a1a6c9ebc4239c8e38758`.

The synthetic `tests/test_candidate_replay.py::_measurement_contract()`
fixture has canonical JSON SHA-256
`cdaafd3e80f5a1c47fe89c03b1cc0a43a55daa6f76dfc6b1dbdef6e0be64b031`.
Its `prospective-replay-test-v1` label and `synthetic/replay-001` scenario
are test-only; neither is an approved experimental configuration. The
replay records the exact hash of the **actual caller-supplied**
measurement contract in each result and rejects a mismatch with the
candidate schema and run descriptors. No approved production
configuration hash exists yet.

## Freeze and execution boundary

Independent scientific review must settle the required verifier checks,
diagnosis category and repeated/no-output rules, action/Comms and safety
schemas, fault/alert and cleanup clocks, post-start infrastructure
taxonomy and signed reattempt rule, exact budgets/seed schedule, and
aggregation/interval method. It must pin clean evaluator source,
checkpoint and per-generation serving identity, independently admitted
adversarial membership, and an approved configuration hash. Final-Test
access, remote hardware/weight transfer, training, live operator/cluster
actions, and release require separate later authorizations. Passing
synthetic tests, matching hashes, and this proposal do not close G13.
