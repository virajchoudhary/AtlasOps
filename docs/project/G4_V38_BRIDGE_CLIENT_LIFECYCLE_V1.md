# G4 v3.8 Bridge Client Lifecycle v1

Prospective execution-blocker amendment only. Historical v3.8 and diagnostics
declarations and all qualification/startup evidence remain unchanged. The
preserved governed-runner startup failure is NOT_QUALIFIED/worker_process_exit,
before any incident reservation, completion request, or P1 proposal. It is not
a model-resolution episode and is not scored.

A healthy authenticated `close` acknowledges client detachment and retains the
same server-owned model worker for the next client. It does not stop, reload or
retry inference. Poisoned close still performs bounded worker termination and
never clears poison. Service lifespan and the local operator retain final
server/model/tunnel cleanup responsibility. Shutdown and inference budgets are
unchanged. HTTP client libraries must not inherit INFO logging that exposes a
transient inference endpoint in operational logs.

The new source binding changes only the bridge source digest and version labels.
Model/tokenizer/adapter identities, runtime locks, scenarios, prompts, decoding,
G4 criteria, approval and verification contracts remain fixed.

After required CI, independent review and merge, use a fresh source-bound
inference server and journals. Reuse existing immutable host artifacts only
after verifying exact runtime/T4/artifacts; perform
one loopback and one tunneled benign qualification, then the governed runner's
required startup qualification without closing the server-owned worker.
Preserve every result. Do not replay the previous startup failure or silently
reuse its evidence. Attempt 016 remains unused until all existing pre-reservation
checks pass. Explicit human approval is required for any exact P1 mutation.
G4 must validly pass before the authorized matched Validation campaign.
No Test/Leaderboard, training/tuning, GRPO, paid compute or operational
credentials on the inference host are authorized.
