# G9 observation journal v1

**NON_EMPIRICAL / NOT_CERTIFIED / DISCONNECTED. No resume authority.**

The optional `ObservationJournal` adds append-only local event records to the
observation-first software candidate. It does not select a live lifecycle,
authorize training, supply approval, repair a cluster or approve a recovery
protocol. Existing non-journal calls retain their in-memory evidence path.

## Ordering

The caller explicitly opens a fresh journal context before trainer use. The
candidate writes `group_started` before invoking lifecycle setup,
`group_observed` before generation, `sample_started` before pre-action
admission and `sample_execute_started` before the action callback. It writes
`group_finished` after the lifecycle's finish callback has returned or raised.
Each successful append flushes and fsyncs its opened file descriptor before
allowing the next callback. A write failure blocks continuation.

An execution-start marker proves only that the callback was about to be
invoked. It does not prove whether the action started, completed or had an
external effect. A finish marker preserves the observed callback outcome,
not objective cleanup or successful gradient optimization. The independent
journal group ID is not an approval or reward-binding token.

## Content and safety

The fixed event schema retains scenario/group identity, sequence, observation
and completion digests, timestamps and bounded final status fields. Final
sample projections retain existing reward/scorable and negative status
classifications without adding a score. Prompts, completion text, action
arguments, raw tool/verifier payloads, operator identity, approval tokens and
free-text reasons are excluded.

The writer creates a new regular file exclusively and retains its descriptor;
existing files cannot be resumed or overwritten. Redirecting paths and
detected file replacement are refused. The reader is bounded and read-only;
it does not repair, truncate, rewrite or normalize stored evidence. These
checks are not a signed external attestation or proof against a hostile
same-user filesystem administrator.

## Crash and recovery interpretation

A process exit between markers leaves incomplete evidence. Missing final
markers, malformed ordering, truncated lines and invalid records are not
completed groups. Reader `RECORDED` means only that the journal structure
contains final records; a recorded failed group remains failed and
non-empirical. Every reader result has `resume_allowed: false`.

Do not replay an action or clear an attempt marker from these records. Future
live recovery requires independently verified current environment and
scenario cleanup, model/checkpoint state, protocol and operator authority.
No such recovery is implemented here. File fsync protects successful writes
to the degree supported by the filesystem; it is not a power-loss, network
volume or directory-entry durability guarantee.

## Verification

Model-free tests check callback ordering, failure retention, bounded content,
exclusive creation, truncated/invalid journals and a child process terminated
during lifecycle setup. The existing installed-TRL CPU contract checks remain
separate from durability and live evidence. Frozen protocols, historical
records, preparation plans and production training admission are unchanged.
G4 remains `NOT_PASSED`, G9 `REOPENED`, and Stage 15 `NOT_CERTIFIED`.
