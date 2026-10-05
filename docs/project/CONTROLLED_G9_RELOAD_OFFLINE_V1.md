# Controlled G9 Verified-Offline Reload v1

Prospective reload-mechanism-only amendment, approved by the project lead
on 5 October 2026. No training, reward, parent, split, budget or gate change.
Execution remains conditional on merge, required CI and Luna Max review.

The prior private Kaggle preflight denied `unshare --net` with exit 1,
Operation not permitted. Preserve that negative environment evidence exactly:
raw SHA-256 `d800594329e90f76eb04863535f1216056cafbdcd80eafd81d2259391c359464`.
It is not GRPO/model evidence and that mechanism is not retried on Kaggle.
`network-namespace-v1` remains supported on capable hosts.

## Alternative Profile

Explicit `--reload-profile kaggle-verified-offline-v1` is separate from the
default namespace route. Neither failure falls back to the other.
Before launching the dedicated fresh process, root observes Kaggle Internet
**disabled** in the private notebook, records the fresh observation time,
source SHA and launcher PID in an external receipt, and pins its raw hash.
This records operator/UI evidence, not cryptographic provider attestation.
Receipt age is bounded to one hour. No notebook kernel reload qualifies;
launch a fresh Python process from the private context.

Before loading models, the process rejects proxy settings, sets
`HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1`, and probes TCP directly at
three fixed public IPv4/IPv6 IP/port pairs, each with a two-second timeout.
Any successful connection, refusal/reset or unknown error is NOT_VERIFIED.
Only timeout, permission denial or explicit route-unavailable errors qualify.
Preserve target/port, timeout,
connected flag and exception class/errno only; no response bodies, tokens,
URLs, proxy values or operational secrets. Failed bounded probes are
evidence for those routes, not universal egress proof by themselves.

After those probes, install a permanent CPython audit/socket guard plus a
Linux x86_64 seccomp TSYNC filter before model loading.
The Python guard precedes model-library imports; the syscall guard is
installed after imports and an empty socket-descriptor check, still before
any model/tokenizer/adapter load. It rejects non-literal-loopback socket connects,
connect_ex, datagram sendto/sendmsg, external DNS resolution, raw/non-IP
sockets, fork and subprocess network fallback. Seccomp also traps native
connect/sendto/sendmsg/sendmmsg calls in every thread; all socket sends
(including loopback) are conservatively blocked. It latches attempted forbidden
operations even when a library catches PermissionError; any latch makes
the reload NOT_VERIFIED. The Kaggle profile admits no inherited socket
descriptors and traps native socket/socketpair creation. All socket IPC,
including Unix IPC and loopback, is conservatively unavailable under this
profile; ordinary file/pipe I/O remains available.
No proxies, tunnels or remote lookup fallback are permitted.

The combined claim rests on the independently observed Kaggle Internet-off
setting, unsuccessful direct probes, explicit local-only pinned loaders,
permanent process guard and byte inventories, not merely offline flags.
The syscall filter closes the native socket bypass; inability to install
it is NOT_VERIFIED, not permission to use Python flags alone.
No operational credentials are present.
Missing prerequisites or uncertain Internet state refuse verification.

Rehash complete pinned Base, original v17 parent and GRPO output inventories
before and after reload; retain the unchanged raw manifest pins.
Every Base/tokenizer/adapter load uses `local_files_only=True`; Base revision
is unchanged. The saved tokenizer is part of the pinned output inventory.
No model generation, Test/Leaderboard access or evaluation occurs.
Fresh process plus finite loaded LoRA tensors and unchanged inventories
are required. Any external attempt, missing artifact, hash mismatch or
fallback lookup yields NOT_VERIFIED, never a successful reload.

Local acceptance validates this exact identified profile, context evidence,
probe targets/results, flags and clean guard latch in addition to the
existing training/checkpoint/export requirements. The user-authorized
2-step/2-generation seed-2026 Train-only $0 pilot is unchanged. No training
may start before this amendment is merged.
