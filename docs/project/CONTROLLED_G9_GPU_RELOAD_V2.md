# Controlled G9 Local GPU Reload v2

Prospective runtime-only profile `kaggle-verified-offline-gpu-v2`, separate
from the unchanged namespace and Kaggle v1 profiles. No training is permitted
until the focused amendment passes required CI, independent Luna Max review
and merge. Preserve the failed unshare preflight and failed GPU smoke exactly;
neither is a model/GRPO result. G4 is frozen NOT_PASSED and G8 remains unmet.

## Discovery

Private Kaggle Internet must be OFF with fresh hash-bound context and failed
bounded direct probes. Offline flags and all local-only loader requirements
remain unchanged. No Base/tokenizer/parent/GRPO artifact is loaded during
runtime discovery.

The exact Python 3.12.11 / 72-package runtime trace identified native
`socket(AF_UNIX, SOCK_SEQPACKET | SOCK_CLOEXEC, 0)`, SO_PASSCRED, an optional
MPS control connection, and an abstract CUDA listener. CUDA discovered both
T4 devices with every connection and send denied. The optional MPS connection
is not permitted. The guard returns ENOENT without connecting during discovery;
IP socket creation, socketpair, accept, message receives/sends, io_uring,
native fork/exec and Python subprocesses are blocked.

Only the traced Unix socket class can be created. Its local bind/listen
operations do not provide an external route. A second irreversible filter
is installed before validating remaining descriptors. Only the exact inert
`@cuda-uvmfd-<PID namespace inode>-<process PID>@` seqpacket listener may remain.
Unknown, connected, accepted, IP, duplicate or missing sockets refuse reload.

## Local Runtime Artifacts

Triton performs `file -b <interpreter>`, `/sbin/ldconfig -p`, and compilation
of its pinned local cuda_utils source on an empty cache. These package-only
setup operations occur before guarded reload, without loading model artifacts.
The local runtime cache inventory and interpreter hash are separately bound
in the offline receipt. No compiler or subprocess is allowed during reload.
The exact current-interpreter architecture query is answered from its verified
x86_64 ELF header; other architecture queries are refused. Missing/changed
cache files fail closed, never trigger an allowed compiler fallback.

## Sealed Reload

Before any pretrained artifact load, tighten the filter against all socket
creation, connects, sends, accepts, message receives, bind/listen and native
fork/exec. The existing local listener is inert: it cannot connect, accept,
send or receive descriptor-passing messages. All model loads remain
`local_files_only=True`, pinned and without remote code or network fallback.
Rehash complete Base, original v17 parent and GRPO inventories before and
after reload. Any forbidden attempt, artifact miss, fallback or hash mismatch
is NOT_VERIFIED. Model claims never establish recovery or reward.

The 5 October model-free six-path smoke passed in the exact runtime with tiny
random artifacts, no backward/update and no pretrained weights. Complete local
archive SHA-256:
`5e0057446b17adbb01391d59619ae1d9f1575af2214927aff318ba369ce6b87b`.
The earlier smoke bundle remains unchanged at
`582f1cf4c74832efe0df49218a746e5ffa4d1dcb7718f06a41b84f22e268d202`;
only the final bundle qualifies the current receipt/filter implementation.
Native bind is constrained by the admitted local socket class, not inspection
of the pointed-to address. Python bind requests and the residual listener
are checked against the exact PID-namespace/PID-bound name.
This proves runtime compatibility only, not training or gate success.
The authorized Train-only 2-step/2-generation $0 replacement pilot is unchanged.
