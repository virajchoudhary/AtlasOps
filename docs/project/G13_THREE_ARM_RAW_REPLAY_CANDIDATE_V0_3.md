# G13 three-arm raw replay candidate v0.3: diagnostic source integrity

**Status: PROSPECTIVE SOFTWARE CANDIDATE / NOT FROZEN / NON-EMPIRICAL.**
This revision supplements the preserved
[v0.2 candidate](G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_2.md), without
altering v0.1, frozen evidence, or the partially selected measurement
directions. It does not approve a scorer, model, source, final-Test access,
training, a live cluster action, or deployment. G13 remains `REOPENED`
and the package `NOT_CERTIFIED`.

G6 now writes its generated run ID and **requested model name** in each
episode row, including failed rows. The native three-arm adapter requires
all G6 rows to agree with the caller's run/model declaration and reports
the identity as raw-bound **requested-name only**. This does not identify
the actual weights serving any response: an Ollama alias and pre/post tag
observations still lack per-generation immutable-serving attestation.
G8 rows may lack these fields; the adapter keeps both identity fields
`UNBOUND` when any required row identity is absent.

For G6 and G8 purported successful diagnostic rows, the adapter enforces
the respective native response field rules within its scorer-hashed source
and requires the stored parsed prediction to agree with the saved raw model
response. Duplicate JSON object keys at any depth, missing, malformed,
non-finite, or contradictory successful records are rejected before a
`diagnosis_output` event is emitted. Actual failed rows remain
`model_failure` observations. This checks input consistency only; it
does not change G6/G8 diagnostic scoring, supply fault/alert/action/
verifier evidence, authenticate external source bytes, or make the replay
empirical. The v0.2 envelope-time limitation remains unchanged.

## Source byte anchors

These SHA-256 values identify the reviewed source bytes, not an approved
protocol, model checkpoint, or empirical run. The ordered five-entry
runtime source manifest is hashed as canonical JSON (sorted keys, compact
separators, UTF-8) for `comparison_scorer_sha256`:

| Source | SHA-256 |
| :--- | :--- |
| `bench/candidate_adapters.py` | `b2f00f99ba0c5524c0157a4f8d46b89501bb65ab95394a803956c7fe5dea3e18` |
| `bench/candidate_lineage.py` | `396ff6d389f2b83631d8b5b50c6a08c74f9df42317dbd48a9d6a2825eb956aa9` |
| `bench/candidate_measurement.py` | `920e73560ddd162a888d5e9698a1a3b788a8d362db8b4d8402e86bccc3834702` |
| `bench/candidate_replay.py` | `9fe0862cd332bdf56d0f4c4ff7ce877abb3d310ae83e5c5e4a357e080bd33cfb` |
| `bench/episode_membership.py` | `c598ab421638db9cf94c2f827c39e2023cc81eb47e413160db68289e853015c8` |

`comparison_scorer_sha256`:
`44dfbcee1607a6623efcdaa5bf8e192112d69d77db7e936eb8f1f26db7a07013`.
The separate G6 emitter source `bench/zero_shot_baseline.py` has SHA-256
`281abfec6f17704a065d2fe0897baf49b25dc8e8d19e0b93a9f5009d13816dcc`.
The v0.2 G9 training source anchor remains a historical candidate snapshot,
not a source or serving attestation for this revision.

Before any claimable comparison, the project still needs independently
authorized fault and alert evidence, a failed objective pre-action verifier,
compatible controlled episodes for all three arms, approved D4-D9
decisions, checkpoint and serving identity, and a frozen protocol.
Do not fill missing fields from caller declarations or historical summaries.
