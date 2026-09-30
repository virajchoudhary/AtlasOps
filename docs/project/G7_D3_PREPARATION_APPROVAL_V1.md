# D3 Preparation Approval v1

**APPROVED FOR PREPARATION ONLY. Training NOT AUTHORIZED. G7 PARTIAL.**

Project lead: Viraj Choudhary. Recorded 30 September 2026.
Authority: explicit user instruction in this project chat:

> I approve `train-candidate-v1` for preparation of a bounded synthetic SFT
> feasibility pilot, with the limitations already documented in the D3
> review/audit. This is not authorization to train yet.

This records the actual preparation decision; it is not a signed empirical
protocol, GPU entitlement, budget approval, model-weight transfer authorization,
named SFT execution permit, or live P1 approval. No broader approval is inferred.

## Bound Data and Accepted Limitations

- Corpus version `train-candidate-v1`, row schema `atlasops-sft-candidate-v1`.
- Corpus raw/canonical-LF SHA-256:
  `19606e4fec300f641c7c8b8a989497367a444a870d3491f225004c01df3ee5fd`.
- Adjacent manifest raw SHA-256:
  `35c9fd63328ef1319f616f2a23a025be38ad99c594dcd8bb38b733e0ff44c67c`.
- Construction commit `e802dd3a5522c30d3919952954e86827fa96caa1`.
- Frozen independent audit:
  `artifacts/evidence/stage7/candidates/train-candidate-v1/quality_audit.md`.
- Frozen data remains unchanged. Historical fixture remains rejected.

The lead accepts the documented limitations for preparation: 16 synthetic
Train scenarios and 17 case groups are not independent real incidents; teacher
behavior is hand-authored; Diagnosis/Remediation/Comms patterns remain repeated;
tool success frequencies are synthetic; some resolution/refusal supervision
comes from the controller/host rather than model calls; blocked Comms rows do
not describe coordinator fallback notifications. None establishes model quality,
live safety, generalization or empirical gate closure.

## Authorized Work

Resolve immutable Qwen2.5-7B-Instruct/tokenizer metadata, recommend remote
NVIDIA hardware, freeze a reproducible environment, stage only small tokenizer
metadata, perform all-row CPU token/mask/truncation checks, implement and test
hash-bound launch admission, preserve failures, and finalize acceptance/reload
requirements. No new scenario or held-out outcome may influence these choices.

Model weights, inference, SFT/GRPO, paid compute/GPU resources, live Kubernetes,
faults, final-Test outcomes and deployment remain prohibited. D1/D2 resource
and transfer authority and named execution approval remain separate.

## Remaining Execution Decision

After non-live preparation, a future approval must bind the chosen host and
verified entitlement/budget, immutable base/tokenizer files, environment image
and installed runtime provenance, corpus/manifest/template/preflight/lock
hashes, precise pilot settings, fresh run ID/output location, retention and
independent reload plan. Execution approval must be independently reviewed
and pinned in the launch gate; a command-line flag cannot grant it.
