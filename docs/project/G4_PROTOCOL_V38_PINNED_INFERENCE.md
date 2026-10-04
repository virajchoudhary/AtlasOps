# G4 Protocol v3.8 Pinned Inference

Prospective amendment only. G4 remains NOT_PASSED until a new valid causal
golden incident passes the unchanged SF002 predicate and verified cleanup.
Attempt 015 is terminal, inconclusive, and unscored. It must never be retried.
The v3.6/v3.7 declarations and historical evidence remain unchanged.

## Inference Basis

Use `Qwen/Qwen2.5-7B-Instruct` and tokenizer revision
`a09a35458c702b33eeacc393d103063234e8bc28`, with the existing paired
NF4/float16/SDPA loader and verified unchanged v17 artifact. G4 uses Base
(adapter disabled) for every role. Validation uses the same loader and
transport, enabling the adapter only in SFT. No tuning or training is allowed.

The existing integrated contracts freeze seed 1337, greedy decoding,
temperature 0, top_p 1, and 512 new tokens. Retain 600-second model-load and
request deadlines, timeout poisoning/cancellation cleanup, durable raw
failure capture and same-arm episode binding. The bridge makes one request
per generation, without replaying failed model output. The coordinator's
historical HTTP retry settings remain declared but are not used by this bridge.

Only model inference may run on a free remote T4. Tool execution, Kind,
Chaos, objective verification, P1 decisions and operational credentials stay
local. The bridge uses a distinct transient inference-only authentication key;
it exposes no operational endpoints and receives no operational credentials.
Remediation uses the existing direct-action adapter and exact-action P1
permit path, as in integrated Validation. The internal `rl_policy` backend
label denotes action execution only; it does not load or train GRPO.
Inherited external-judge flags are disabled. Missing judgment reward stays
unavailable. Qualification/runtime and the raw journal are linked in G4
evidence, with a final raw-byte journal digest in a separate immutable sidecar.
G4 execution is capped at 3,600 seconds after benign qualification. Validation
retains its existing 3,600-second per-episode cap. One transport request per
generation means no replay of failures; any predefined infrastructure-invalid
retry remains separately preserved and cannot improve a model-result selection.

## Admission And Attempt 016

Before reservation, require a benign Base response through the selected
bridge, bounded model loading/generation, and final pinned artifact checks.
This is infrastructure qualification, not incident evaluation. Preserve
qualification failures separately; they consume no G4 attempt.

Run `EXP-STAGE4-SF002-016` once from the exact reviewed, merged clean main.
Keep existing role/tool/approval/settling/telemetry/scenario safety contracts.
Only directly changed runner/bridge/qualification source pins are updated.
Zero-Chaos and baseline gates still precede reservation and injection.
P1 requires a real human decision on the exact proposed action; missing,
rejected and timed-out approval fails closed. Model claims and cleanup do
not establish recovery. Preserve every interruption or inconclusive result.

Only after valid G4 PASS may the already-approved matched Validation
campaign proceed. No Test/Leaderboard, paid compute, SFT tuning, GRPO,
G13, or final certification is authorized.

## Integrity References

`artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json`
clarifies only two prospective references to the preserved external archive.
The reported mismatches were raw CRLF hashes compared against LF-normalized
digests. Fresh byte checks reproduce both original LF digests, so the original
links are valid. The index records both raw and LF-normalized identities. The
original interruption, attempt, YAML, cleanup, inventory, and logs are not
edited. This clarification corrects the audit interpretation, not the outcome.
