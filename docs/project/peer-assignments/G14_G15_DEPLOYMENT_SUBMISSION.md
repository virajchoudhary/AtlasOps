# Peer assignment E — G14 deployment acceptance and G15 scientific submission

**Starting software baseline:** `b23d6cb4319b77b4d8606934e401141bcb01d23f` (20 October 2026). This is NOT an experiment source freeze, approval or scientific certification.

**Agent entrypoint:** Read this assignment fully, then [peer onboarding](../PEER_ONBOARDING.md), [peer handoff tracker](../PEER_HANDOFF.md), [completion matrix](../COMPLETION_CHECKLIST.md), [master pipeline](../MASTER_PIPELINE_STATUS.md), and [proposed execution protocol](../PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md). Verify current `origin/main` and your draft PR head before editing. Use your own clean worktree, preserve original evidence and all negative outcomes. Make maximum progress on permitted offline work, test it, and add evidence to this PR. Never promote an empirical gate from tests or mock fixtures.

## Current status
The read-only local demo and verified software review packaging are complete; G14 and G15 are **PARTIAL**. The user-reported final-main source package at commit `b23d6cb4319b77b4d8606934e401141bcb01d23f` includes 801 tracked files and a separately verified review bundle. A file-path-only reference to `C:/AtlasOps/.audit/peer-handoff-20261019/` is **not** an uploaded artifact; ask the owner to provide the exact review ZIPs, receipts and SHA-256 checksums securely. Do not assume a remote peer can access the original Windows disk. Scientific report certification remains **NOT_CERTIFIED**.

## Do now
- Read [peer onboarding](../PEER_ONBOARDING.md), [peer tracker](../PEER_HANDOFF.md), [completion matrix](../COMPLETION_CHECKLIST.md), `docs/AtlasOps_Technical_Report.md` and `docs/project/DEMO_RUNBOOK.md`.
- From a clean checkout, verify tracked source, commit identity, Python lock and frontend build. Check the exact source ZIP independently with the supported archive verifier; distinguish original Git object bytes from permitted CRLF worktree representations, and record any skipped third-party legal checks.
- Reproduce non-empirical local demo, static access-control/security checks, package hash/link/license checks, UI/browser checks and evidence separation. Do not mistake simulated rehearsal for a live incident.
- Identify a named peer-host owner, approved audience and independent reviewer. Prepare a non-destructive host acceptance checklist: clean installation, exact-source demo startup, authentication and network boundaries, fail-closed approvals, error handling and reproducible operator observations.
- Finalize report/slides/reviewer-facing package **only with truthful gate claims**. Keep G4/G9 failure, missing G6/G8/G12/G13 evidence, G10/G11 exclusion and absence of any actual external submission explicit.
- Upon every later reviewed code merge, regenerate the source-specific archive and independent receipt for the actual final main SHA; never relabel an earlier package as current.

## Permission boundaries
Host-owner approval before peer-host startup, operator/security access or configuration changes; project-lead approval for scientific assertions; submission owner approval before actual external academic upload. Public deployment, credentials and production remediation are not implicitly authorized. Unavailable certified results may require a defensible negative-results scope, explicitly approved by the project lead; never silently reinterpret the original specification.

## Definition of done
- **G14:** named permitted peer host, actual independently checked startup/security/operator acceptance, exact source and evidence.
- **G15:** complete verified final-source package plus scientifically accurate final report and presentation, independent claims review, authorized submission and actual external receipt.
- G15 may remain blocked after packaging if research or approval is absent.

**PR completion checklist**
- [ ] Source archive and owner receipts actually shared and independently hashed
- [ ] Peer-host owner and review target explicitly identified
- [ ] Accepted non-destructive peer-host checklist and evidence
- [ ] Final scientific claim review against actual raw outcomes
- [ ] Authorized external submission and receipt independently verified
