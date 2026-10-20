# Peer assignment A — Experimental environment and G4 causal incident

**Starting software baseline:** `b23d6cb4319b77b4d8606934e401141bcb01d23f` (20 October 2026). This is NOT an experiment source freeze, approval or scientific certification.

**Agent entrypoint:** Read this assignment fully, then [peer onboarding](../PEER_ONBOARDING.md), [peer handoff tracker](../PEER_HANDOFF.md), [completion matrix](../COMPLETION_CHECKLIST.md), [master pipeline](../MASTER_PIPELINE_STATUS.md), and [proposed execution protocol](../PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md). Verify current `origin/main` and your draft PR head before editing. Use your own clean worktree, preserve original evidence and all negative outcomes. Make maximum progress on permitted offline work, test it, and add evidence to this PR. Never promote an empirical gate from tests or mock fixtures.

## Current status and real blockers
G3 has historical local Kind evidence, but NOT a fresh healthy cluster. G4 is **NOT_PASSED/FROZEN**. The current experimental readiness record found Docker unavailable, stopped WSL, Kind unreachable, seven source-pin mismatches and no qualified serving, telemetry or real operator channel. Attempt 017 was a completed negative; 018 has only a completion marker without independently verified recovery/cleanup. These are preserved facts, not something to repair by editing old evidence.

## Do now (without experimental authority)
- Use `python -B -m config.g4_demo_candidate` on a clean checkout; preserve its exact mismatch findings. Establish the fresh final software source and proposed model/source/configuration inventory **without changing old pins to force a pass**.
- Review `config/g4_demo_candidate.py`, `scripts/run_stage4_golden_incident.py`, `agents/coordinator.py`, and existing G4 source/telemetry/approval/cleanup tests for any reproducible fail-closed defect. Add tests for actual defects; don't refactor working paths.
- Prepare a named-host readiness worksheet: exact Docker/WSL/Kind/Kubernetes context, existing containers and Chaos objects, workloads, pod UID/container ID, two advancing timestamped telemetry observations, genuine Alertmanager admission, model+tokenizer/adapter identity, authenticated exact-action operator, run-owned cleanup. `../PEER_HANDOFF.md` specifies read-only checks and restrictions.
- Verify host RAM/VRAM/storage rather than assuming proposal values. An earlier local 4 GiB GPU did not meet the proposed training/model requirement. Identify safe alternatives as proposals, not approvals.
- Independently review the updated causal protocol and one-episode resource/cleanup budget. Preserve negative and unscorable cases.

## Separate human approvals before moving further
Host owner: any WSL/Docker/Kind restoration, resource use, changes to services and selected cluster. Project lead: formally reopen frozen G4, approve source/model/protocol freeze and one fresh attempt. Operator: exact live action, expiry, fault and cleanup permissions. No approval is inherited from a merged PR.

## Definition of done
- **Offline handoff:** independently reviewed readiness record, complete reproducible preflight, source/pin mismatch resolution *proposal*, frozen prospective protocol and clear NO-GO/GO evidence.
- **Scientific G4:** newly permitted causal SF002 episode with real fault, alert, model response, authenticated approval, exact remediation, independently observed recovery before cleanup and verified run-owned cleanup. Raw outcome and negative evidence retained; independent reviewer recomputes the verdict.
- G4 is **not passed** if the actual episode is blocked, inconclusive, aborts or ends without verified recovery.

## Dependency handoff
Only accepted G4 evidence permits proposing separately approved G6/G8 incident campaigns. Read [deferred scientific work](../DEFERRED_RESEARCH_HANDOFF.md) and the [evidence index](../../EVIDENCE_INDEX.md) before any new run.

**PR completion checklist**
- [ ] Current-source offline preflight and blockers reproduced
- [ ] Any genuine offline defects fixed and tested
- [ ] Independent protocol/source review recorded
- [ ] Required host/project-lead/operator approvals obtained and linked
- [ ] Newly authorized episode and independent objective acceptance recorded (or honest BLOCKED/NEGATIVE)
