# AtlasOps: Autonomous Multi-Agent Incident Response on Kubernetes via Generative AI, Hybrid Runbook Recommendation, and Online Policy Optimization

**Authors:** AtlasOps Academic Team
**Repository fork:** `virajchoudhary/AtlasOps`
**Upstream baseline:** `Harikishanth/AtlasOps` at `bf9bd197c9f4a05ae55ade254802a9eef1a74356`
**Status:** Research report draft; full pipeline certification is not established.
**Evidence review:** 24 September 2026.

## Abstract

AtlasOps combines an incident triage, diagnosis, approval, remediation, verification, and communications chain with a runbook recommender and a proposed online reinforcement learning policy. The project preserves upstream architecture and adds software paths for frozen scenario splits, model training, evaluation, and evidence collection. The latest completed golden-incident attempt in the preserved Stage 4 series did not pass. Saved zero-shot, SFT, GRPO, and ablation numbers previously presented as empirical were mock or hardcoded outputs. Current implementation work improves those paths, but a genuine trained SFT/GRPO checkpoint and the required live evaluations are not yet evidenced. This report records the method and open claims without assigning unobserved resolution, reward, or time-to-resolve values.

## 1. Introduction & Background

The university team continues the upstream AtlasOps repository under its MIT license. The academic goal is to test whether multi-agent reasoning, runbook recommendation, and online policy optimization can improve incident response while preserving a human approval boundary and objective environment verification. A benchmark model answer is a proposal; operational resolution comes from the environment verifier. The frozen scenario catalogue contains 28 scenarios with Train(16), Validation(6), and Test(6) identities. Split definitions and source hashes are recorded in the project configuration and Stage 5 evidence.

## 2. System Architecture & Multi-Agent Flow

The intended flow is Alert → Triage → Diagnosis → observed evidence → Recommender Top-K → Safety/Approval → Remediation policy → one atomic tool action → environment verifier → next policy state or Comms. The recommender is advisory. A P1 mutation requires explicit human approval; timeout, rejection, and missing approval block mutation. The verifier's `env_resolved` field controls resolution, regardless of an agent's claim. Current integration contracts are local software tests and do not by themselves prove live behavior.

### Evidence boundary

Benchmark scenario truth, expected root cause, known-good remediation, and frozen verifier predicates are scoring inputs. They must be withheld from model-facing alert, recommendation, and policy state until after inference or action generation. Mock/test adapters are marked non-empirical and cannot close an experimental gate.

## 3. Academic Workstreams & Methodology

### 3.1 Generative AI and SFT

The Stage 7 corpus manifest records 64 examples drawn from the 16 training scenarios, along with the approved Qwen2.5-7B-Instruct QLoRA configuration. Corpus preparation and software tests are evidence of data and implementation only. A completed training manifest, hashed adapter, loss history, and successful real checkpoint evaluation are still required before claiming an SFT effect. G6 and G8 diagnosis evaluations must preserve raw model responses and score only after prediction; they do not imply environment resolution.

### 3.2 Runbook recommendation

The repository implements BM25, popularity/random baselines, and a hybrid content, co-occurrence, and prior ranker. Its saved Stage 10/11 results use scenario-derived labels, not observed historical incident-to-runbook feedback. The original 28-row corpus covers four of twelve catalogued runbooks and remains historical evidence. A corrected, separately preserved synthetic corpus excludes seven scenarios without defensible single-label runbooks: 21 rows span Train(12), Validation(5), and Test(4), with nine runbooks covered. A local hybrid fit on the 12 Train rows yields Test Hit@3=1.0000 and MRR@3=0.7083 on four synthetic Test rows; BM25 yields 0.7500 and 0.6250. These small synthetic measurements do not establish a historical-feedback effect or production generalization. Ranking scores are not calibrated success probabilities.

### 3.3 Online GRPO

The scientific contract is state → trained policy → structured action → one tool execution → settling → objective verifier → reward → next state. Training optimization uses Train only; Validation and Test remain isolated. Existing local tests exercise direct action parsing, approval, policy checks, verifier-grounded resolution, checkpoint validation, and exact policy-output execution. The trainer now has a provenance manifest and rollout ledger; the evaluator rejects absent or incomplete checkpoints. These tests do not establish a trained GRPO checkpoint, safe shared-cluster rollout, or an empirical reward improvement.

## 4. Empirical Evaluation & Multi-Model Ablations

| Gate | Current evidence-backed status | Missing evidence |
| :--- | :--- | :--- |
| G4 golden incident | NOT_PASSED | A valid live run with clean preflight, authorized P1 action, verifier resolution, and proven postflight cleanup. |
| G6 zero-shot | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | Actual approved base-model inference with raw predictions and split/model hashes. |
| G7 SFT | PARTIAL | Completed approved training and usable hashed adapter. |
| G8 SFT evaluation | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | Real checkpoint inference over frozen evaluation split. |
| G9 GRPO | REOPENED | Safe real training trajectories, completed checkpoint provenance, and real evaluation. |
| G12 integration | IMPLEMENTED / EMPIRICAL EVIDENCE MISSING | Real integrated policy execution with preserved end-to-end evidence. |
| G13 ablation | REOPENED | Complete real variant-by-partition artifact matrix and computed uncertainty. |

The latest completed Stage 4 result among attempts 009–014 is `artifacts/evidence/stage4/EXP-STAGE4-SF002-010.json`: the gate failed, the environment was not resolved, and the diagnosis targeted the wrong service. Attempts 009 and 011–014 are interrupted or inconclusive; some cleanup states remain unverified. These outcomes are retained.

The old Stage 13 `ablation_benchmark_results.json` contains predetermined profiles, including the previously reported 100% resolution, 18-second TTR, and 0.918 reward. They are not empirical measurements. No supported comparative effect size or confidence interval is reported here. A real ablation runner must consume authenticated raw variant artifacts.

## 5. Demonstration & Operator Console

The repository contains a local demo launcher and operator console with a safe-mode path. Static/local tests support implementation readiness only. Startup prerequisites, live service health, infrastructure portability, credential configuration, human approval operation, and postflight cleanup need target-specific verification before a deployment claim. No public or production deployment is certified by this report.

## 6. Conclusion & Attribution

AtlasOps has substantial software implementation and a preserved negative experiment history. The strict research pipeline remains incomplete. The next scientific work is to establish a sound G4 live contract, obtain approved base-model and trained-checkpoint evidence, and run the predefined evaluation/ablation matrix without exposing Test truth during inference. No result, model metric, TTR, or environment resolution is fabricated in this report.

The full upstream Git history, MIT license, and original attribution remain part of the fork.
