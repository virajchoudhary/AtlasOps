# G4 local offline progress record — 21 October 2026

**Scope:** offline preflight, read-only host probes and bounded source review only.
No Docker/Colima/WSL/Kind start, cluster change, model load, inference, fault
injection, remediation or Final-Test access was performed. Nothing was pushed.
G4 remains **NOT_PASSED / FROZEN**; scientific status remains `NOT_CERTIFIED`.
This record is not independent review and is not an approval of any kind.

## Exact source and host

| Item | Observed value |
| --- | --- |
| Source checked | `origin/main` = `b23d6cb4319b77b4d8606934e401141bcb01d23f`, clean full-history clone |
| Host | Apple M4 (arm64), 10 GPU cores, 16 GiB unified memory, no CUDA device; macOS (Darwin 24.6.0) |
| Free storage | 115 GiB on the data volume (460 GiB total) |
| Python | 3.12.11 venv from `pip install -e ".[dev]"` (the Windows lock is not installable on macOS) |
| Node | 25.9.0 |

## Source-only preflight (`python -B -m config.g4_demo_candidate`)

Reproduced exactly: `status=SOURCE_MISMATCH`, `historical_profile_unchanged=true`,
`active=false`, `runtime_qualified=false`, `launch_authorized_by_this_report=false`,
`empirical_pass=false`. Mismatched pinned files (7):
`agents/tools/chaos.py`, `scripts/run_stage4_golden_incident.py`,
`agents/coordinator.py`, `demo/read_api.py`, `demo/incident_monitor.py`,
`training/grpo_environment.py`, `agents/verifier.py`. No pin was edited.

### Finding: the declared basis commit does not reproduce the pins

`config/g4_demo_candidate.py` declares `SOURCE_BASIS_COMMIT = 18e3010b…`.
Hashing every pinned path from Git objects shows:

| Commit | Pinned paths that do not reproduce |
| --- | --- |
| `18e3010` (declared basis) | `scripts/run_stage4_golden_incident.py`, `demo/operator.py`; `config/g4_protocol_selection.py` and `config/g4_launch_authority.py` **do not exist** |
| `7ed0dec` (commit that introduced the candidate) | none — all 27 pins reproduce |
| `b23d6cb` (current main) | the 7 files listed above |

The pins therefore describe the tree of `7ed0dec`, not its parent `18e3010`.
The inspector still fails closed, so this is a provenance-labelling defect, not
a safety defect. It was **not** changed here: correcting a frozen candidate's
recorded basis is a project-lead decision. Proposal: any fresh candidate should
record the commit whose tree reproduces every pin and test that property.

### Source drift since the pinned tree (`7ed0dec..b23d6cb`)

| File | Change | Commits |
| --- | --- | --- |
| `agents/coordinator.py` | +225/−41 | `f13374f`, `cbd617d`, `553c448`, `87e3dc2` |
| `scripts/run_stage4_golden_incident.py` | +261/−25 | `f13374f` |
| `agents/tools/chaos.py` | +326/−0 | `f13374f` |
| `demo/read_api.py` | +61/−2 | `f13374f` |
| `agents/verifier.py` | +56/−1 | `f13374f` |
| `training/grpo_environment.py` | +13/−0 | `f13374f` |
| `demo/incident_monitor.py` | +8/−6 | `f13374f`, `cbd617d` |

Resolution proposal (not applied): freeze a new candidate at a reviewed final
commit, re-pin all 27 paths from that commit's tree, and have an independent
reviewer confirm the drift above before any reopening request.

### Bounded source review

Reviewed `553c448` and `87e3dc2` (post-mutation observation in
`agents/coordinator.py`). A missing observer, a non-object observer result, an
observer exception, or any result other than `env_resolved=true`+`passed` or
`env_resolved=false`+`failed` ends the remediation turn as `escalated` with
`terminal_block.reason=post_mutation_observation_unknown`; only the explicit
passed pair yields `resolved`. No fail-closed defect found in this scope.
The remaining drift (notably `chaos.py` and the incident runner) was covered
only by the test suite, not by line-by-line review.

### Fail-closed audit of the G4 path (21 Oct 2026, second pass)

Read `agents/stage_a.py`, `agents/approval.py`, `agents/approval_http.py` and the
causal predicate/acceptance code in `scripts/run_stage4_golden_incident.py`.

| Concern | Finding |
| --- | --- |
| Stale telemetry accepted as fresh | No: baseline samples must be −5…60 s from apply start; recovery needs ≥2 identity-bound post-action samples ≥30 s apart, newest ≤60 s old |
| Ambiguous workload/container identity | No: CPU samples must match the bound namespace/pod/container; exactly one injected container record required |
| Missing fault identity (`None == None` UID) | No: `bind_fault` requires a truthy `uid` equal to the created UID, and recovery cannot be recorded without a bound fault |
| Unauthenticated approval | No: `X-AtlasOps-Key` checked with `hmac.compare_digest` |
| Remediation not tied to approved action | No: permit bound to incident ID + SHA-256 of canonical action JSON, single-use; Stage A also allows only the exact fault-removal arguments |
| Missing expiry | No: 300 s permit/approval expiry; 480 s action and 540 s recovery limits; fault expiry enforced |
| Cleanup conflated with recovery | No: recovery must precede verification and expiry; cleanup must start after the primary record and is scored separately (`uid_bound_cleanup`) |
| Negative/inconclusive promoted | No: any exception or missing field leaves every acceptance criterion false |
| Operator identity | **Known gap, not new:** `approved_by` is free text behind one shared key — not a per-operator authenticated identity. Already listed as unqualified in the tracker; not changed here |

No new defect was found, so no code changed.

## Read-only host probes (stopped at first unavailable engine)

| Probe | Result |
| --- | --- |
| `bash infra/local/setup_local.sh --check` | `ERROR: Docker daemon is not running.` |
| `docker --context colima info` / `colima status` | daemon socket unavailable; `colima is not running` |
| `docker --context desktop-linux info` | daemon socket unavailable |
| `kind get clusters` | fails: cannot connect to Docker daemon |
| `kubectl --context kind-atlasops-local get nodes` | context exists in kubeconfig; API `127.0.0.1:60023` connection refused |

Per the tracker, probing stopped here. Nothing was started, reset, pruned or
switched. Existing containers, Chaos objects, workloads, pod UID/container IDs,
telemetry, Alertmanager admission, model identity and operator channel could
therefore **not** be inspected and remain unqualified.

## Capacity against the proposal

The proposal's 24 GiB VRAM serving/training allowance is not met: this host has
no CUDA device and 16 GiB of unified memory shared with the OS. It cannot serve
the pinned v3.8 inference runtime as specified. Alternatives, as proposals only:
(a) a host-owner-named CUDA host with ≥24 GiB VRAM, measured before approval;
(b) a reviewed lower-memory plan that states any model/precision change explicitly.

## Verdict

**Offline handoff: NO-GO.** Blockers: source mismatch and basis-label finding,
no running container engine or cluster, no qualified model/telemetry/alert/operator
path, insufficient measured accelerator capacity, and no G4 reopening.
Approvals still required, all unobtained: host owner (restoration, resources),
project lead (reopen G4, freeze source/model/protocol, one attempt), operator
(exact action, expiry, fault, cleanup).

## Record metadata

- Branch `handoff/g4-environment-incident-20261020`; remote base `3ef1459`; source `main` `b23d6cb`.
- Gate status before: NOT_PASSED / FROZEN. After: **NOT_PASSED / FROZEN** (unchanged).
- Files added on this branch: this record; [prospective protocol candidate](G4_PROSPECTIVE_PROTOCOL_CANDIDATE_20261021.md).
  File edited: the brief's checklist (one box ticked: preflight reproduced).
- No code changed; no hash, pin or historical evidence edited.
- Final SHAs, full test counts and skip reasons: `~/Documents/AtlasOps-review/local-closure-20261021/README.md` (local only).
- **No remote push occurred. No empirical result was fabricated.**
- Addendum (22 Oct 2026): this record was later pushed with the local closure work in
  `chore/local-closure-20261022`; the statements above describe the state when it was written.
