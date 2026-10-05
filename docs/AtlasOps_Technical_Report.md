# AtlasOps: Multi-Agent Incident Response with Generative AI and Policy Optimization

**Authors:** AtlasOps Academic Team

**Repository:** `virajchoudhary/AtlasOps`

**Upstream baseline:** `Harikishanth/AtlasOps` at `bf9bd197c9f4a05ae55ade254802a9eef1a74356`

**Evidence state:** 5 October 2026
**Certification:** NOT_CERTIFIED

## Abstract

AtlasOps is a university continuation of an open-source multi-agent site reliability
engineering system. This report describes its four-agent workflow, approval and
verification controls, frozen scenario design, one bounded QLoRA supervised fine-tuning
artifact, a matched Base-versus-SFT Validation diagnostic, and a final controlled GRPO
negative result. The SFT adapter was independently reloaded from a fresh process. In the
matched diagnostic, mean token-set F1 was 0.16875 for Base and 0.15935 for SFT, a paired
difference of -0.00940, with valid diagnostic JSON in 6/6 responses per arm. No
diagnostic improvement was observed. The GRPO pilot produced no reward-driven learning
and no acceptable SFT+GRPO checkpoint. G4 remains NOT_PASSED, the original G8 live
incident-resolution criterion remains unmet, and the project is NOT_CERTIFIED. These
negative and limited findings define the current scientific result.

## 1. Problem and Motivation

Incident response combines alert triage, evidence gathering, diagnosis, change control,
environment verification, and communication. AtlasOps retains an architecture in which
specialized agents propose and carry out bounded work under tool policies and human
approval. The project asks whether multi-agent reasoning and policy optimization can
support this workflow while making resolution depend on observed system state.

The continuation distinguishes software capability from operational outcome. A model
response is a proposal. An objective environment verifier, rather than an agent claim,
determines whether a required condition holds. Earlier upstream performance and
hardware statements remain historical unless reproduced under the current project
protocol.

## 2. Architecture

AtlasOps has four specialized agents:

- **Triage** classifies an alert and identifies the evidence needed next.
- **Diagnosis** interprets observed telemetry and proposes a cause.
- **Remediation** may propose a bounded action after policy checks and required approval.
- **Comms** reports the verified outcome and records unresolved or blocked work.

The coordinator binds agent turns to tools and policy. The Safety and Approval Gate
controls whether a proposed mutation may proceed. A separate objective verifier checks
the environment before a resolution is recorded. The preserved design also includes an
incident correlator, circuit breaker, and HMAC-chained audit trail.

The registry contains 24 SRE tool wrappers. Role policy exposes 19 to autonomous agents;
registration alone does not grant access. Local tests exercise software interfaces,
policy behavior, and control flow. They do not establish live cluster operation or a
successful incident response.

## 3. Safety and Governance

The implementation separates agent proposals from authorized side effects. P1
remediation requires explicit approval. Rejection, timeout, or a missing decision
blocks the mutation. Policy checks also restrict each role to its allowed tools. The
objective verifier controls resolution claims, and recorded evidence remains available
for provenance review.

The scenario catalog and split definitions preserve Train, Validation, and Test
membership. Current evidence uses the permitted bounded Train and Validation work.
Historical and synthetic results are labeled separately from current empirical
evidence. Mock tests and local CI are evidence of software behavior only. They do not
certify safety, infrastructure health, or scientific gate closure.

## 4. Data and Frozen Evaluation Design

The repository contains 28 frozen scenario manifests with Train(16), Validation(6),
and Test(6) identities. Split governance records scenario membership, hashes, and
verifier predicates. This report does not present a final-Test or Leaderboard result.

The v17 SFT run used `train-candidate-v1`, a synthetic, scenario-derived corpus of 68
rows from the Train split. Its manifest records 16 scenarios and a canonical corpus
SHA-256 of
`19606e4fec300f641c7c8b8a989497367a444a870d3491f225004c01df3ee5fd`. The corpus is
bounded training material; it is not a set of observed historical operator
interactions or evidence of incident improvement.

The Base-versus-SFT campaign used one alert-only Validation response per scenario for
each arm. Its six-scenario diagnostic is not a live tool-use or incident-resolution
evaluation. The run record discloses that an earlier review read Test split
documentation. It also records that no Test catalog records or Test outcomes were
inspected or scored.

## 5. QLoRA SFT Method

The v17 run used `Qwen/Qwen2.5-7B-Instruct` and its pinned tokenizer at revision
`a09a35458c702b33eeacc393d103063234e8bc28`. The approved bounded run used one free
Tesla T4, Python 3.12.11, CUDA 12.6, and a verified 72-package environment. The
configuration used four-bit NF4 quantization with double quantization and FP16 compute,
LoRA rank 16 and alpha 32, one epoch, batch size 1, gradient accumulation 8, learning
rate `0.0002`, maximum sequence length 8192, and seed 2026.

Training ran from clean source commit
`af629ada4629e76c194e66a357935981f42e2c90`. The run completed nine optimizer steps.
The reported training diagnostics describe this synthetic Train-only run; they do not
measure SRE performance.

## 6. SFT Training Evidence

The preserved run is `sft-pilot-free-t4-20261003-v17`. Its result records a completed
adapter with 392 LoRA tensors. The adapter weight SHA-256 is
`f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff`, and the run
manifest SHA-256 is
`7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440`.

Independent verification loaded the adapter in a fresh process with network isolation,
checked finite LoRA tensors, and rehashed the base inventory before and after reload.
The checkpoint manifest remained unchanged. The reload check performed no inference.
The preserved adapter archive SHA-256 is
`59fea5f55ff5ecb4ca86d679bd5f8eaa856f1881b514020d92af9014cfb7c418`. The
[canonical result](../artifacts/evidence/stage7/free-t4-v17/RESULT.json),
[manifest](../artifacts/evidence/stage7/free-t4-v17/sft_run_manifest.json), and
[reload record](../artifacts/evidence/stage7/free-t4-v17/reload-v17.json) preserve the
lineage and verification details.

This establishes the existence and reloadability of one bounded artifact. It does not
show incident improvement, successful G4 resolution, or general model capability.
The final gate review promotes G7 to PASS for this bounded artifact/reload
target. Its free-T4 profile prospectively approved an isolated hash-locked
venv instead of an OCI image. The original repeatability-tolerance condition
applies before a separately authorized replicate, which this work does not
claim. [The Stage 7 acceptance review](project/STAGE_7_SFT_DATA_AND_TRAINING.md)
records the reasoning without modifying the original evidence.

## 7. Base-versus-SFT Validation Result

The matched campaign used the same pinned base model, tokenizer, prompts, generation
settings, and quantized model for both arms. Base inference disabled the PEFT adapter;
the SFT arm enabled v17. Six scenarios were scheduled, returned, and scored in each
arm. All 12 outputs conformed to the diagnostic JSON schema.

| Metric | Base | SFT | SFT minus Base |
|---|---:|---:|---:|
| Mean token-set diagnostic F1 | 0.16875 | 0.15935 | -0.00940 |
| Diagnostic JSON conformance | 6/6 | 6/6 | 0 |
| Inference or schema failures | 0 | 0 | 0 |

**No diagnostic improvement was observed.** The difference is descriptive for six
alert-only scenarios. It does not support a general superiority or inferiority claim.
The campaign did not measure resolution, action validity, safety, reward, or time to
resolve.

The [result record](project/BASE_SFT_VALIDATION_RESULT_V1.md) links the
[run manifest](../artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/run_manifest.json),
[raw responses](../artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/raw_episodes.jsonl),
[scored episodes](../artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/episodes.jsonl),
and independent recomputations. The run manifest SHA-256 is
`878b01e079d294ad35a6ab73365c01e1a3d7cf138a2afaae72b73ecbb59d49b4`. The scored
ledger SHA-256 is
`99a45c1cdb08c8c23f4b6d9c7c51153e00c6528bf976049cae6291bd16ad39c2`.

## 8. Controlled GRPO Method

The controlled G9 track used a bounded capacity simulator on the frozen Train scenario
`single_fault/sf-002`. It was a declared training analogue, not a Kubernetes cluster,
Chaos Mesh experiment, digital twin, or live incident evaluator. The protocol tied an
observed simulator state to a v17-initialized policy, one structured action, policy and
approval checks, a simulator transition, objective checks, a reward, and the next
state. Invalid or blocked actions received the pilot's prospective penalty of -1.

The one replacement pilot was predeclared for two optimizer steps and two completions
per generation group. It did not use Validation, Test, or Leaderboard data. Equal
rewards can yield zero advantages, and invoking an optimizer alone is not evidence of
reward-driven learning. The protocol and result are documented in
[Controlled G9 Admission v1](project/CONTROLLED_G9_ADMISSION_V1.md) and
[the final negative result](project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md).

## 9. Final Negative GRPO Result

The genuine replacement pilot completed two optimizer steps and generated four policy
completions. All four actions were malformed or blocked, each received reward -1, and
both advantage groups were zero. The run established no reward-driven GRPO learning
and saved no acceptable SFT+GRPO checkpoint.

A final aligned inference-only diagnostic then sampled eight sequential outputs using
the v17 tool-call template and a strict, predeclared parser. Zero of eight outputs was
admissible under that contract. It took zero optimizer steps, produced no gradients or
reward-based updates, and left all 392 LoRA tensor hashes unchanged. This is a negative
result under the tested serialization contract. It does not show that semantically
valid actions are impossible or that the population probability is exactly zero.

The comparison observed an interface mismatch: v17 used Qwen ChatML tool-call
envelopes, while the pilot used bare state JSON and a different three-key action
contract. The evidence did not establish that mismatch as the sole cause. No checkpoint
was accepted, and the final G9 track is experimentally unsuccessful and frozen.
Canonical details, caveats, and hashes are in the
[final result](project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md), the tracked
[local verification](../artifacts/evidence/stage9/final-aligned-diagnostic-v1/LOCAL_VERIFICATION.json),
and the [independent review](../artifacts/evidence/stage9/final-aligned-diagnostic-v1/INDEPENDENT_REVIEW.json).

## 10. Multi-Agent and Integrated Pipeline Implementation

The repository implements a multi-agent coordinator, role-specific tool policies,
approval controls, an objective verifier, audit logging, and direct-action policy
interfaces. The local software path and tests cover important contracts such as
fail-closed P1 handling and verifier-controlled resolution. The online GRPO route is
serial for live rollouts. The controlled simulator pilot is separate from that live
route.

Implementation coverage is distinct from empirical integration. No accepted
SFT+GRPO checkpoint or real integrated policy episode is available. G6 and G8 do not
have a full incident-resolution baseline or SFT evaluation. G12 software coverage does
not demonstrate a real policy/environment run. Historical predetermined Stage 13
profiles do not establish an ablation result. Read
[the current Master Pipeline inventory](project/MASTER_PIPELINE_STATUS.md) for the
authoritative gate classifications; this report does not promote a gate.

## 11. Evaluation Boundaries

The current evidence supports one bounded SFT artifact and reload, one small
Base-versus-SFT diagnostic, one negative controlled GRPO pilot, and one negative
aligned action diagnostic. It does not support claims of incident improvement,
operational safety, or superiority.

G4 remains NOT_PASSED. Attempt 015 was terminal INCONCLUSIVE and unscored, 016 was a
pre-fault abort and non-result, and 017 completed with a negative outcome. No attempt
018 exists. The 015 integrity index is tracked at
[`EXP-STAGE4-SF002-015.integrity-index-v1.json`](../artifacts/evidence/stage4/EXP-STAGE4-SF002-015.integrity-index-v1.json).
The 015 and 017 raw bundles are preserved outside this repository. The 017 archive
SHA-256 is recorded in
[Controlled G9 Admission v1](project/CONTROLLED_G9_ADMISSION_V1.md). The original G8
live incident-resolution criterion remains unmet.

Earlier upstream reports of 54%/68%/82% resolution, 0.481/0.601/0.729 reward,
100% resolution, 18-second TTR, and 0.918 reward remain historical or predetermined
outputs. They are not current team results. G10 and G11 are OUT_OF_SCOPE for the
required GAI + RL completion scope; their retained recommender work remains optional
historical research.

## 12. Read-Only Demonstration

The repository's standalone Gradio demo provides a local evidence-browsing path:

```powershell
python -m demo.launcher --host 127.0.0.1 --port 7860
```

The launcher binds to localhost by default and enters safe mode. Scenario selection
reads checked-in manifests. The demo invokes no kubectl command, injects no fault,
starts no agent incident workflow, and performs no cluster cleanup. It requires no
Docker, Kind cluster, GPU, or model endpoint. It is not a public deployment, live
remediation demonstration, or empirical gate result. The current read-only scope and
acceptance boundary are described in
[Stage 14](project/STAGE_14_DEPLOY_FINAL_DEMO.md).

## 13. Limitations

- The SFT corpus is synthetic and scenario-derived, and contains 68 Train-only rows.
- The SFT artifact is reloadable, but the measured diagnostic difference was negative.
- The Validation diagnostic contains six alert-only scenarios per arm and measures no
  live action or incident-resolution outcome.
- The controlled GRPO pilot had zero reward-driven advantages and saved no acceptable
  checkpoint.
- The aligned diagnostic produced 0/8 admissible actions under one frozen parser
  contract. This does not estimate a population success probability precisely.
- No G4 incident passed. The G8 live criterion remains unmet, and no SFT+GRPO arm or
  final-Test outcome is available.
- The read-only demo does not establish deployment safety or current cluster health.
- The full scientific pipeline remains NOT_CERTIFIED.

## 14. Reproducibility and Evidence

The principal artifacts are preserved with run metadata, hashes, and independent
checks:

| Result | Canonical evidence | Key digest |
|---|---|---|
| v17 SFT run | `artifacts/evidence/stage7/free-t4-v17/` | Adapter `f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff`; manifest `7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440` |
| Base-vs-SFT Validation | `artifacts/evidence/stage8/base-sft-validation-v1/base-sft-validation-20261003-v1/` | Run manifest `878b01e079d294ad35a6ab73365c01e1a3d7cf138a2afaae72b73ecbb59d49b4` |
| Controlled GRPO pilot | `docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md` and its preserved archive record | Archive `39834cc3911bf6e32deec509a1c139d905e17aee802563abcb2ce3802f23cd33` |
| Final aligned diagnostic | `artifacts/evidence/stage9/final-aligned-diagnostic-v1/` | Archive `6137ce1d88eada54152b0c58bb7dd02f9c8758ddacf73adfafffb5a8f598e6c8`; samples `84e30346b8948cca44b164f69106fbfbba8bb189f31e892aa0ff7f6d4034439b` |
| G4 chronology | `docs/project/CONTROLLED_G9_ADMISSION_V1.md` and `artifacts/evidence/stage4/` | Attempt 017 archive SHA-256 is recorded in the admission document |

The package inventory at `artifacts/SUBMISSION_MANIFEST.json` records hashes and sizes
for selected tracked checkout files. Those hashes establish file integrity only. They
do not establish model quality, live safety, or scientific gate closure. The manifest
and summary are generated by `scripts/package_submission.py`; focused integrity checks
are in `tests/test_stage15_submission_package.py`.

## 15. Conclusion and Deferred Research

The [deferred research handoff](project/DEFERRED_RESEARCH_HANDOFF.md) isolates
the remaining experimental dependencies. The
[evidence index](EVIDENCE_INDEX.md) records compact tracked evidence and
external archive boundaries. The completed presentation/review package remains
distinct from full scientific certification or actual external submission.

The continuation now has a documented multi-agent implementation with explicit
approval and verification boundaries, a genuine bounded v17 SFT adapter that reloads
successfully, and a preserved matched Base-versus-SFT diagnostic. That diagnostic
showed no improvement. The controlled GRPO experiment ended negatively, with no
reward-driven learning and no accepted SFT+GRPO checkpoint. G4 remains NOT_PASSED, and
G8's original live incident-resolution requirement remains open.

Only future, separately authorized scientific work can change those empirical
conclusions. It would require new evidence under the original approved G4/G8 criteria
and a new reviewed GRPO protocol with a valid trainable checkpoint. This report does
not authorize another G4 attempt, GRPO training, Test or Leaderboard access, model
inference, cluster mutation, or paid compute. The upstream Git history, MIT license,
and original attribution remain preserved.
