# G9 async observation bridge v1

**NON_EMPIRICAL / NOT_CERTIFIED / DISCONNECTED. G9 REOPENED.**

The [observation-first candidate](G9_OBSERVATION_FIRST_SOFTWARE_CANDIDATE_V1.md)
expects synchronous callbacks because pinned TRL invokes generation and reward
hooks synchronously. AtlasOps's approval waiter, localhost approval listener
and direct-action environment use asyncio. A temporary `run_until_complete`
cannot keep the approval listener responsive while synchronous generation
holds the caller thread.

`training.grpo_async_lifecycle.ManagedAsyncObservationLifecycle` is a managed
adapter for an explicitly injected async lifecycle. Its context owns a
background event-loop thread. The caller uses synchronous `begin`,
`before_action`, `execute` and `finish`; the underlying methods run on the
same continuously serviced loop via `run_coroutine_threadsafe`. Gate
creation, requests, waits, HTTP callbacks and environment steps must all
belong to that owner loop. No caller should mutate loop-owned gate state
from another thread.

## Lifecycle and boundaries

The adapter has no default callbacks, infrastructure connection or training
entrypoint. Construction does not run a callback. Enter the context before
trainer use and leave it after the trainer has finalized its group. Duplicate
entry, calls before entry/after close, overlapping calls and calls from the
owner-loop thread are refused. The last rule prevents synchronous self-wait
deadlock. Callback methods must be awaitable.

There is no bridge action timeout or implicit retry. The injected operator
gate retains its own configured timeout. An operator timeout is a gate
decision, not evidence that an arbitrary bridge callback had no effect.
Callback exceptions/cancellation propagate to the candidate's existing failure
and `finish` handling. If the caller's synchronous wait is interrupted while
its callback is still pending, the bridge waits for that same callback to
settle before releasing its single-call lock and propagating the interruption.
It does not cancel or retry an action merely because the caller was
interrupted; the action may have had an effect. This prevents `finish` from
overlapping an unfinished action. Context close cancels/drains remaining tasks and joins the
thread; task termination is not proof that a tool had no external effect or
that cluster cleanup succeeded. A callback that blocks synchronously or
suppresses cancellation can delay shutdown; no hard termination or safe
rollback guarantee is made.

The lifecycle must explicitly close its listener and finalize its resources
in `finish`. Closing a bridge alone does not call `finish`, supply approval,
verify zero Chaos, restore a cluster or resume an interrupted episode. Use a
fresh lifecycle/bridge after close; do not carry old requests or permits into
a new run. Durable restart/recovery and the live group reset/repetition rule
remain unapproved and unimplemented.

## Non-live verification

Unit tests cover lifecycle ordering, thread/loop ownership, concurrency,
exceptions/cancellation, task draining and fresh contexts. The separate
`test_grpo_async_approval_integration.py` combines the real localhost-only
authenticated HTTP listener and `ApprovalGate`, the observation-first
candidate, and `DirectPolicyEnvironment` with injected tool and verifier
stubs. It verifies listener responsiveness during held synchronous
generation, authenticated actual decision delivery, approval permit
consumption, rejection/timeout refusal and listener shutdown.

The test's Kubernetes context string and execution flag exercise the
environment admission interface only: every tool and verifier is injected;
no real cluster is contacted. Synthetic timeout values and test decisions
are not changes to an experimental protocol or real operator approvals.
This proves neither live safety nor equivalent reset state, model residency,
gradient alignment, trained checkpoints or scientific improvement.

Production `training/grpo.py` remains unchanged and blocked. Frozen
protocols, historical evidence, scorer formula and attempt 015 are not
modified by this bridge. Separate protocol, hardware, model, cluster and
execution approval is still required before any live use.
