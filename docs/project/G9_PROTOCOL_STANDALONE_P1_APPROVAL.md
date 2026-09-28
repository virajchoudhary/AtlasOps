# G9 prospective standalone P1 operator channel

Status: software implementation candidate, **not an executed GRPO run or G9
gate result**. G9 remains `REOPENED`. This revision does not change the frozen
G4 v3.3 profile, prospective G4 v3.4 profile, or historical evidence.

## Execution boundary

Standalone `training.grpo` and `bench.grpo_eval` retain their explicit live
opt-in and named Kubernetes context. Their new `--enable-p1-approval` option
is an additional operator-channel opt-in, not permission to execute a fault,
train, evaluate, or remediate. Without it, P1 mutation remains blocked.

The option requires a nonempty `ATLASOPS_API_KEY` environment variable before
training creates its output directory or evaluation loads a checkpoint. No
demo literal or checked-in secret is a fallback. The key is neither a CLI
argument nor a trainer/reward-object field, run manifest, URL, log entry, or
submission asset. The operator must provision it securely on the host.

The training reward callback starts an authenticated HTTP listener on an
ephemeral `127.0.0.1` port in the same process and event loop as its
`ApprovalGate`, before zero-Chaos preflight or fault application. It logs only
the local address, not the key or token. Each serialized batch closes the
listener after the callback returns or fails. Evaluation starts its listener
before loading a policy and entering the split evaluator. The evaluator does
not itself apply or clean a scenario fault; its pre-action verifier still
requires a reachable, unresolved fault supplied by a separately governed
procedure. Operators must use the runner host or a separately reviewed secure
access path; the listener must not be exposed publicly.

Only authenticated `GET /approval/pending` and `POST /approve` are served.
The pending request displays the exact parsed tool, arguments, and canonical
action digest, plus the runner-pinned Kubernetes context and scenario ID.
The context/scenario display is trusted runner metadata, not part of the
action digest; the permit cannot be moved to another gate instance or
incident through the callback API. The operator posts its token, `approved`
or `rejected`, and a
nonblank name with the `X-AtlasOps-Key` header. The name is operator-supplied,
not independently attested personal identity. The gate waits up to 300
seconds. Only an explicit approval issues a one-use permit bound to the
trusted per-rollout incident ID and exact action digest. Mismatch, replay,
rejection, timeout, missing identity/key, stale token, cancellation, or
restart cannot authorize dispatch. A new process has a new in-memory gate;
it cannot resume an interrupted incident or retrospectively approve one.

Training derives severity from the observed alert, strips untrusted approval
and runtime-control fields, and serializes fault/action/cleanup cycles.
Distributed `WORLD_SIZE`/rank or initialized multi-process torch execution
is refused before a callback contacts the cluster. Standalone empirical
evaluation treats every mutating action as P1 because its public-state file
is not an authoritative severity source. It overwrites a caller-provided
incident ID with a fresh trusted ID and rejects caller approval fields.
The post-action verifier, settling, objective reward, checkpoint provenance,
and blocked/unscorable behavior remain authoritative. Missing alerts and
failed fault application stop training with null reward rather than assigning
a numeric policy score. Empirical failure records retain safe error classes,
not arbitrary exception text. The run manifest and summary record only the
transport mode, timeout, and identity limitation.

## Non-live verification and remaining acceptance

Synthetic tests exercise authenticated approval and rejection across an
actual subprocess boundary; wrong/missing authentication, timeout, replay,
restart, exact-action permit consumption, training listener startup before
cluster preflight, single-writer refusal, missing-key refusal, and evaluator
severity override. They do not establish an actual human decision, cluster
health, safe fault cleanup, a trained adapter, or held-out performance.

A future operator must separately review this protocol and the actual source
SHA, CI, model/checkpoint identity, approved host and cluster context, current
zero-Chaos state, emergency cleanup, and evidence location before any run.
No live G9 invocation, G4 attempt-015 reservation, real P1 operator request,
or T0 occurs as part of this change.
