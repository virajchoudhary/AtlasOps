# G13 three-arm raw replay candidate v0.4: run-terminal provenance

**Status: PROSPECTIVE SOFTWARE CANDIDATE / NOT FROZEN / NON-EMPIRICAL.**
This revision supplements the preserved [v0.3 source snapshot](G13_THREE_ARM_RAW_REPLAY_CANDIDATE_V0_3.md).
It does not change the partially selected measurement directions, authorize
final-Test access or an experiment, or certify G13.

For a hash-checked native G9 stream, the candidate replay source summary now
reports `terminal_record_ref`: the observed `run_completed` or
`run_interrupted` event name, the verified source SHA-256, and its one-based
JSONL line. A stream with no run terminal reports null. This includes an
unscoped `run_interrupted` with no active episode; the reference points back
to the pinned raw bytes without copying arbitrary terminal fields or
inventing a scenario episode. The adapter still retains the complete raw
event sequence. G6/G8 row sources and common-episode input are unchanged.

The reference proves only where a terminal appears in caller-supplied bytes
that match the caller's digest. It does not authenticate the source, decide
incident eligibility or infrastructure invalidation, compute reward, relax
strict empirical membership, or convert a failed or interrupted run into a
claimable result. Output remains `NOT_CERTIFIED`; G13 remains `REOPENED`.

## Source byte anchors

These SHA-256 values identify this candidate's reviewed source bytes, not
an approved scientific protocol or empirical run. The ordered five-entry
runtime source manifest is hashed as canonical JSON with sorted keys,
compact separators, and UTF-8 for `comparison_scorer_sha256`:

| Source | SHA-256 |
| :--- | :--- |
| `bench/candidate_adapters.py` | `b2f00f99ba0c5524c0157a4f8d46b89501bb65ab95394a803956c7fe5dea3e18` |
| `bench/candidate_lineage.py` | `396ff6d389f2b83631d8b5b50c6a08c74f9df42317dbd48a9d6a2825eb956aa9` |
| `bench/candidate_measurement.py` | `920e73560ddd162a888d5e9698a1a3b788a8d362db8b4d8402e86bccc3834702` |
| `bench/candidate_replay.py` | `57e823e0ffd9cf00b5e3474e281c13e1a637264b03250252b833b4e1475b0054` |
| `bench/episode_membership.py` | `c598ab421638db9cf94c2f827c39e2023cc81eb47e413160db68289e853015c8` |

`comparison_scorer_sha256`:
`74b6d9e7ff7e897e48aff2489ff3e91e796b99b021cf8adbfccc10c37e65f93c`.
The v0.3 hashes remain a historical source snapshot, not current runtime
anchors. D4-D9 decisions, compatible controlled episodes for all three arms,
independent source/model attestation, and a frozen protocol remain required.

## Later evaluator-identity consistency repair

The native G9 replay now compares the producer's `evaluator_source.code_sha`
and `source_state` with the independently validated run descriptor's evaluator
commit and clean-state declaration. Contradictory or malformed fields are
rejected, including conflicting start and terminal metadata. Missing fields
remain `UNBOUND`; they are not filled from the descriptor. The source summary
reports `evaluator_identity_binding` separately from run/model identity.
Legacy synthetic `git_sha`/`git_dirty` fields are checked too; if both schemas
are present, contradictory values are rejected rather than preferring one.
Its evaluator tree digest remains `DESCRIPTOR_ONLY_NOT_RAW_BOUND`, not an
authenticated raw-source attestation.

The source hashes above remain the historical v0.4 snapshot, not hashes of
this later implementation. This repair changes metadata consistency only,
not the measurement formulas, eligibility, pending decisions, or protocol
freeze. Synthetic regression results do not close G13 or certify an episode.
