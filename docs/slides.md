---
marp: true
theme: default
paginate: true
backgroundColor: "#F7F9FB"
color: "#1B2933"
style: |
  section { font-family: Arial, sans-serif; padding: 48px 64px; }
  h1 { color: #12333B; font-size: 2.5em; }
  h2 { color: #12333B; font-size: 1.65em; }
  strong { color: #007D85; }
  small, footer { color: #52636B; }
---

# AtlasOps

## Governed multi-agent SRE research

University continuation of Harikishanth/AtlasOps<br>
Evidence state: 5 October 2026<br>
Research status: **NOT_CERTIFIED**

<!-- Evidence: AGENTS.md; docs/project/GAI_RL_SCOPE_REVISION.md -->

---

## Incident Response Problem

An incident workflow must interpret alerts, gather evidence, make a diagnosis, control
changes, and verify system state.

AtlasOps assigns distinct roles to these steps. A model response proposes work. The
environment verifier decides whether a required condition has been met.

The current evidence describes software, one bounded SFT artifact, a diagnostic
comparison, and a negative controlled GRPO result.

<!-- Evidence: agents/coordinator.py; agents/verifier.py; docs/project/MASTER_PIPELINE_STATUS.md -->

---

## Agent Workflow

1. An alert enters the coordinator.
2. Triage classifies the incident and requests evidence.
3. Diagnosis interprets observations and proposes a cause.
4. Safety policy checks the proposed action and required approval.
5. Remediation dispatches an allowed action after approval.
6. The environment verifier checks required conditions.
7. Comms records the verified outcome or the reason work remained blocked.

<!-- Evidence: agents/coordinator.py; agents/approval.py; agents/verifier.py -->

---

## Four Agent Roles

- **Triage** structures incoming alerts and identifies what evidence to collect.
- **Diagnosis** reasons over observations and proposes a cause.
- **Remediation** uses only its permitted tools after required approval.
- **Comms** reports the verifier-backed result and preserves uncertainty.

<!-- Evidence: agents/prompts/; agents/coordinator.py -->

---

## Safety and Verification

- Role policy exposes 19 of 24 registered SRE tool wrappers.
- P1 mutations require explicit approval. Rejection, timeout, and missing decisions
  block dispatch.
- The objective environment verifier controls resolution outcomes.
- An HMAC-chained audit log records agent actions and incident boundaries.
- Local tests cover software contracts. They do not certify live safety or a successful
  incident.

<!-- Evidence: agents/tool_policy.py; agents/approval.py; agents/verifier.py; agents/audit.py -->

---

## Frozen Evaluation Design

The catalogue contains 28 frozen scenarios.

Train contains 16 scenarios. Validation contains 6. Test contains 6.

The v17 corpus contains 68 synthetic, scenario-derived Train-only rows. The Base-vs-SFT
diagnostic used six alert-only Validation scenarios per arm. No final-Test outcome is
reported.

<!-- Evidence: config/scenario_catalog.py; config/splits.py; docs/project/BASE_SFT_VALIDATION_RESULT_V1.md -->

---

## SFT Artifact v17

Base model: `Qwen/Qwen2.5-7B-Instruct`<br>
Pinned revision: `a09a35458c702b33eeacc393d103063234e8bc28`<br>
Training: one epoch, 9 optimizer steps, free Tesla T4<br>
Method: four-bit NF4 QLoRA, LoRA rank 16, alpha 32<br>
Data: 68 synthetic Train-only examples

Fresh-process independent reload passed. The adapter is a genuine bounded artifact.
Its existence and reload do not demonstrate incident improvement.

<!-- Evidence: artifacts/evidence/stage7/free-t4-v17/RESULT.json; artifacts/evidence/stage7/free-t4-v17/reload-v17.json -->

---

## Base-vs-SFT Validation Diagnostic

**Base mean token-set F1: 0.16875**

**SFT mean token-set F1: 0.15935**

**Paired difference: -0.00940**

Diagnostic JSON conformance: 6/6 in each arm<br>
**No diagnostic improvement observed**

The comparison is descriptive. It measures neither resolution nor action validity,
safety, reward, or time to resolve.

<!-- Evidence: docs/project/BASE_SFT_VALIDATION_RESULT_V1.md; artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/summary.json -->

---

## Controlled GRPO Design

The pilot used a bounded capacity simulator and the frozen Train scenario
`single_fault/sf-002`.

The policy observed simulator state before generating a structured action. Policy and
approval checks preceded simulator transition and objective scoring.

This protocol describes controlled synthetic training. It does not reproduce a live
Kubernetes incident or the original G4 recovery criterion.

<!-- Evidence: docs/project/CONTROLLED_G9_ADMISSION_V1.md; training/grpo_controlled.py -->

---

## Controlled G9 Pilot Outcome

- Two optimizer steps produced four policy completions.
- All four actions were malformed or blocked.
- Each completion received reward -1.
- Both advantage groups were zero.
- No reward-driven GRPO learning was established.
- No acceptable SFT+GRPO checkpoint was saved.

The experiment ended as a negative result. The G9 track is frozen.

<!-- Evidence: docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md -->

---

## Final Aligned Action Diagnostic

**Admissible actions: 0 of 8**

Optimizer steps: 0<br>
Reward-based updates: 0<br>
LoRA tensor hashes unchanged: 392 of 392

This is the final negative decision under the tested serialization contract. Zero of
eight does not establish an exactly zero population probability.

<!-- Evidence: artifacts/evidence/stage9/final-aligned-diagnostic-v1/LOCAL_VERIFICATION.json; artifacts/evidence/stage9/final-aligned-diagnostic-v1/INDEPENDENT_REVIEW.json -->

---

## G4 Live Incident Track

Attempt 015: terminal INCONCLUSIVE and unscored<br>
Attempt 016: pre-fault abort and non-result<br>
Attempt 017: completed negative outcome

**G4 remains NOT_PASSED. No attempt 018 exists.**

The original G8 live incident-resolution criterion remains unmet. The G4 raw bundles
for 015 and 017 are preserved outside this repository.

<!-- Evidence: docs/project/CONTROLLED_G9_ADMISSION_V1.md; artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json -->

---

## Historical Claim Boundary

Archived upstream material reported resolution values of 54%, 68%, and 82%, with
rewards of 0.481, 0.601, and 0.729. The continuation did not reproduce those results.

Predetermined Stage 13 profiles include 100% resolution, 18-second TTR, and 0.918
reward. Those values are not observed outcomes.

The charts under `assets/training/` remain historical illustrations and do not describe
current model performance.

<!-- Evidence: README.md; docs/project/MASTER_PIPELINE_STATUS.md; artifacts/evidence/stage13/ablation_benchmark_results.json -->

---

## Read-Only Local Demo

```powershell
python -m demo.launcher --host 127.0.0.1 --port 7860
```

Open `http://127.0.0.1:7860/`. The demo browses repository evidence on localhost. It
requires no model endpoint, GPU, Docker, or Kind cluster. It runs no model inference,
kubectl command, fault injection, approval action, or remediation.

<!-- Evidence: demo/launcher.py; docs/project/STAGE_14_DEPLOY_FINAL_DEMO.md -->

---

## Evidence and Reproducibility

Canonical records preserve source revisions, run configuration, raw results, and
SHA-256 checks. Independent recomputations support the Base-vs-SFT summary. A fresh
process reload supports the v17 artifact identity. The G9 diagnostic preserves raw
samples and before-and-after adapter hashes.

The submission manifest checks tracked-file integrity. Hashes do not certify scientific
gates.

<!-- Evidence: artifacts/SUBMISSION_MANIFEST.json; docs/project/BASE_SFT_VALIDATION_RESULT_V1.md; docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md -->

---

## Limitations

- The SFT corpus is synthetic and scenario-derived.
- The six-scenario Validation result showed no diagnostic improvement.
- The GRPO pilot established no reward-driven learning.
- No acceptable SFT+GRPO checkpoint exists.
- No G4 incident passed, and G8 live resolution remains unproven.
- The read-only demo does not certify deployment or current cluster health.
- The project remains NOT_CERTIFIED.

<!-- Evidence: docs/project/MASTER_PIPELINE_STATUS.md; docs/project/BASE_SFT_VALIDATION_RESULT_V1.md; docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md -->

---

## Conclusion and Deferred Research

AtlasOps now has a governed multi-agent implementation, a reloadable bounded SFT
artifact, a real Base-vs-SFT diagnostic, and a preserved negative controlled-GRPO
result.

The Base-vs-SFT diagnostic found no improvement. The final controlled GRPO track
produced no accepted checkpoint. G4 and the original G8 incident-resolution criterion
remain open.

Any future empirical work requires separate authorization and evidence under the
approved criteria. This presentation does not authorize G4, training, Test access,
cluster mutation, or paid compute.

<!-- Evidence: docs/project/MASTER_PIPELINE_STATUS.md; docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md -->
