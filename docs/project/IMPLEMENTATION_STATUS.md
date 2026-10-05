# AtlasOps implementation status

**Current continuation status: NOT_CERTIFIED.** This is a concise software and
evidence classification, not an independent live-readiness verdict. The
[Master Pipeline inventory](MASTER_PIPELINE_STATUS.md) controls the formal
G0-G15 status and explains preserved negative and mock evidence.
The [GAI + RL scope revision](GAI_RL_SCOPE_REVISION.md) makes the recommender
optional historical work and marks G10/G11 `OUT_OF_SCOPE` for final completion.


| Area | Current classification | Evidence boundary |
|---|---|---|
| Coordinator / four-agent flow | IMPLEMENTED / NON-LIVE TESTED | Triage, Diagnosis, Approval, Remediation, Verifier, and Comms are wired; the legacy advisory Recommender is optional. G4 `NOT_PASSED`: the latest completed preserved attempt is negative, and current cluster/model readiness is not inferred from tests. |
| SRE tool policy | STATICALLY VALIDATED | 24 wrappers are registered, 19 are exposed through role ACLs, and 5 are intentionally unexposed. Side-effect policy and explicit action approval do not prove live backend reachability or safe remediation. |
| Safety / approval | IMPLEMENTED / NON-LIVE TESTED | P1 rejects timeout, rejection, and missing decisions. The prospective v3.4 host listener and standalone G9 exact-action channel have synthetic process-boundary tests; G9 requires a separate opt-in and key, and remains fail-closed by default. A real operator decision and G4 outcome are unverified. |
| Benchmark and model identity | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | The legacy `bench.runner` is mock-only. G6 validates finite numeric prediction confidence and leaves invalid returned predictions unscored, but still requires a real frozen-split run and immutable-serving attestation; matching Ollama tag observations cannot certify a per-generation model digest. |
| SFT corpus and training | PARTIAL | The bounded 68-row synthetic `train-candidate-v1` free-T4 v17 pilot completed and its durably preserved adapter passed independent offline reload ([evidence](../../artifacts/evidence/stage7/free-t4-v17/RESULT.json)). The legacy 64-row fixture remains historical. G8 has no real SFT checkpoint evaluation; no incident gains are claimed. |
| Direct-action GRPO | NOT_PASSED / FROZEN FINAL NEGATIVE | G9 `NOT_PASSED`: the genuine controlled replacement pilot had zero reward-driven advantages; the final aligned inference-only diagnostic had 0/8 admissible canonical actions. No acceptable SFT+GRPO checkpoint exists and no further training retries are authorized. Historical local direct-action contracts remain software evidence; see [final result](CONTROLLED_G9_FINAL_NEGATIVE_V1.md). |
| Environment verifier | IMPLEMENTED / CONTRACT TESTED | Verifier predicates cover 28 frozen manifests and separate agent claims from observed resolution. This does not make the G4 negative result a PASS. |
| Optional historical Recommender Systems | OUT_OF_SCOPE / BOUNDED SYNTHETIC EVALUATION | G10's former PARTIAL and G11's former bounded-offline PASS describe retained scenario-derived results, not current completion gates or incident resolution evidence. |
| Infrastructure configuration | REPAIRED / STATICALLY VALIDATED | Explicit check/apply gates, pinned components, RBAC, and static shell/template tests do not establish current cluster health or target portability. |
| Controlled infrastructure | HISTORICAL LOCAL G3 PASS | Stage 3 acceptance recorded a Kind environment and component APIs, with individual wrapper and trace-ingestion limits. Current cluster health and any GKE deployment require separate verification. |
| Integrated evaluation and ablation | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | G12 lacks a real checkpoint/environment run. G13 remains REOPENED: the prospective three-arm GAI/SFT/SFT+GRPO contract lacks raw outcome recomputation and a frozen protocol. The artifact-driven runner rejects historical predetermined metric profiles. The bounded adversarial generator writes unapproved proposals, not held-out evaluation evidence. |
| Operator UI and submission | PARTIAL / NOT_CERTIFIED | The read-only local console and asset-hashed Stage 15 inventory are useful for presentation. They do not establish a safe deployment, a complete empirical matrix, or final scientific certification. |

No deployment, peer-host experiment, new P1 request, or attempt-015 reservation
follows from this status summary. Frozen v3.3 and historical evidence remain
authoritative for their original runs.
