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
