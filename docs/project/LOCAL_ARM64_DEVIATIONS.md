# Local environment deviations on arm64

`infra/local/setup_local.sh` pins Online Boutique to commit
`98e60f5ee0b643cc00bceb71e6efb89617740432`. That manifest's resource limits are
tuned for amd64 GKE nodes. Two of them do not hold on an arm64 host (Apple
Silicon under Colima or Docker Desktop), so a first `--apply` there fails in a
way that looks like a broken script and is not.

Every deviation here is recorded rather than folded into the pinned manifest, so
the frozen upstream content hash stays intact and the delta is auditable.

---

> [!NOTE]
> **These are applied automatically.** `setup_local.sh` reads the node's real
> architecture and applies D1 only when it reports `arm64`. Nothing here needs
> to be run by hand; the section documents *what* is applied and *why*, and what
> it means for any measurement taken on this host.

## D1 — cartservice and currencyservice memory limits

**Symptom.** `cartservice` and `currencyservice` enter `CrashLoopBackOff`; each
pod's last state is `reason: "OOMKilled", exitCode: 137`. cartservice's readiness
probe also reports `error reading from server: EOF`, which is a consequence of
the kill, not the cause. `loadgenerator` then never leaves `Init`, because its
init container blocks on a frontend that returns 500 while its dependencies are
down.

**Cause.** The pinned manifest sets `resources.limits.memory: 128Mi` for both.
cartservice is .NET and currencyservice is Node.js; on arm64 each exceeds that
ceiling during startup. Six other services share the same 128Mi limit and stay
within it — including `paymentservice`, also Node.js — so this is specific to
these two workloads, not a blanket problem with the limit. It is a property of
the upstream manifest and the host architecture, not of AtlasOps.

**Deviation applied**, by `apply_arm64_boutique_deviation()`:

| Deployment | memory limit | cpu limit | probes |
|---|---|---|---|
| `cartservice` | 128Mi → 384Mi | 300m → 600m | timeout 1s → 5s, liveness threshold 3 → 6 |
| `currencyservice` | 128Mi → 384Mi | 200m → 400m | same |

Raising memory alone stopped the OOM kills but not the restarts: the manifest
also sets `timeoutSeconds: 1` on both gRPC probes, and .NET and Node startup on
a contended arm64 node do not answer within one second. The container then exits
0 on the liveness SIGTERM, which reads as a clean exit rather than a probe
failure — which is why the first round of memory-only fixes looked like it had
worked and had not.

**Ordering.** `kubectl apply` of the pinned manifest reverts these patches, so a
hand-applied fix silently disappears on the next `--apply`. `setup_local.sh`
therefore applies them itself, immediately after the manifest and before the
rollout wait, in `apply_arm64_boutique_deviation()`. A pod that happens to be
running under a reverted limit is not safe — it is one memory spike from being
killed again, which is exactly what a chaos run produces.

**Why this is not a weakened contract.** It changes a resource ceiling, not
behaviour, and touches no scenario, predicate, prompt, tool contract, or
verifier check. `cartservice` is the target of `single_fault/sf-001`,
`cascade/cs-005`, `multi_fault/mf-002`, `multi_fault/mf-004`, and
`named_replays/hist-discord-2022`; for each, the verifier asserts workload
readiness and chaos clearance, both unaffected by the limit. Without the
deviation those scenarios cannot run at all, because the workload never reaches
a healthy baseline.

**Blast radius on measurement.** `named_replays/hist-discord-2022` and
`cascade/cs-005` apply memory pressure to cartservice. A larger ceiling changes
how much headroom those faults have to consume. Any future benchmark run on
arm64 must record this deviation alongside its results; runs made under
different ceilings are not directly comparable.

---

## D2 — loadgenerator start ordering

**Symptom.** `loadgenerator` sits in `Init:0/1` with restarts while the rest of
the stack comes up.

**Cause.** Its init container blocks until `frontend` serves traffic. On a
contended single-node cluster, frontend readiness lags far enough that the init
container times out and retries. It resolves itself once the node settles.

**Deviation applied.** None. This is slow convergence, not failure. Allow the
rollout wait to run rather than intervening.

---

## Reproducing on arm64

```bash
colima start --cpu 6 --memory 9 --disk 60 --runtime docker
python scripts/generate_runtime_secrets.py
PYTHON_BIN="$PWD/.venv/bin/python" bash infra/local/setup_local.sh --check
PYTHON_BIN="$PWD/.venv/bin/python" bash infra/local/setup_local.sh --apply
```

`PYTHON_BIN` must point at an interpreter with `bcrypt` installed; Argo CD
credential derivation needs it. Preflight now checks this in seconds rather than
failing ten minutes in, after the cluster and half the stack already exist.

Setting it explicitly is no longer strictly required: an activated virtualenv is
now selected ahead of whatever `python3` resolves to on `PATH`. This mattered
because Homebrew's `python3` sitting ahead of `.venv/bin` on `PATH` has no
`bcrypt`, so a valid `--apply` aborted purely on PATH ordering. Passing
`PYTHON_BIN` still overrides everything and remains the recommended form for
unattended runs, where no venv is activated.

Docker Desktop is not required. Colima provides a headless daemon that `kind`
uses directly, which also avoids Docker Desktop's GUI-gated startup — on the
host this was developed against, Docker Desktop's backend started but its VM
never came up without interactive input.
