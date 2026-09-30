# G13 three-arm raw replay candidate v0.2: source-time provenance

**Status: PROSPECTIVE SOFTWARE CANDIDATE / NOT FROZEN / NON-EMPIRICAL.**
This revision supplements the preserved
[v0.1 candidate](G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_1.md); it does not
revise the partially selected v0.2 measurement directions, approve a scorer,
or authorize training, a live cluster, P1 action, final-Test access, or
deployment. G13 remains `REOPENED` and the package `NOT_CERTIFIED`.

The native G9 event adapter now retains an existing event envelope's
`recorded_at` as `source_event_recorded_at` beside its raw line reference.
Missing envelope time remains missing; malformed time fails closed. An
envelope's persistence time is **not** an action, verifier, fault, alert
delivery, or cleanup event clock. It is not copied into the candidate
measurement contract's `recorded_at` field, does not establish episode
admission or TTR, and cannot make the replay empirical.

Separately, G9 training's rollout ledger records host-observed return states
and timestamps for zero-Chaos preflight, Chaos apply, alert query, and
cleanup. Its non-synthetic alert-query result is not proof of delivery, and
an apply return is not independent fault authorization or observation.
The candidate adapter consumes G9 evaluator event streams, **not** these
training ledger rows. No automatic lineage or scoring bridge between them
is claimed. The original v0.1 raw-membership, null-metric, source-identity,
and partition-access limitations remain in force.

## Source byte anchors

These SHA-256 values identify the reviewed source bytes for this prospective
candidate, not a model, protocol approval, or empirical run. The ordered
five-entry runtime source manifest is hashed as canonical JSON (sorted keys,
compact separators, UTF-8) for `comparison_scorer_sha256`:

| Source | SHA-256 |
| :--- | :--- |
| `bench/candidate_adapters.py` | `16ff9b44468893c5f1a77454b507c580e3ba1c05e22fa971fa80520de6b144f8` |
| `bench/candidate_lineage.py` | `396ff6d389f2b83631d8b5b50c6a08c74f9df42317dbd48a9d6a2825eb956aa9` |
| `bench/candidate_measurement.py` | `920e73560ddd162a888d5e9698a1a3b788a8d362db8b4d8402e86bccc3834702` |
| `bench/candidate_replay.py` | `9fe0862cd332bdf56d0f4c4ff7ce877abb3d310ae83e5c5e4a357e080bd33cfb` |
| `bench/episode_membership.py` | `c598ab421638db9cf94c2f827c39e2023cc81eb47e413160db68289e853015c8` |

`comparison_scorer_sha256`:
`4979d03a0aac57874cf8da4ce00faaf84c8698ae6c17be8a1803ce965365464e`.
The separate G9 training-ledger source `training/grpo.py` has SHA-256
`0c5870003196312b5c200d72748853d2892646d993050ae0a7a9d8657de6dcc6`.
Neither source hash authenticates an external observation or a served model.

Before any claimable three-arm comparison, obtain independently authorized
fault, observed fault, delivered alert, exact action/verifier/cleanup event
times, compatible base/SFT/SFT+GRPO raw episodes, approved D4-D9 decisions,
checkpoint and serving identity, and an independently reviewed frozen
protocol. Do not fill those fields from live flags, model claims, envelope
timestamps, or historical summary values.
