---
title: AtlasOps
emoji: 🚨
colorFrom: red
colorTo: blue
sdk: docker
app_port: 7860
pinned: true
short_description: Evidence-led multi-agent SRE research demo (not certified)
tags:
  - agents
  - multi-agent
  - reinforcement-learning
  - amd
  - rocm
  - sre
  - kubernetes
---

# AtlasOps

## Governed multi-agent SRE research

AtlasOps is a university continuation of Harikishanth/AtlasOps. It preserves the
upstream architecture and adds governed agent, evaluation, and evidence workflows. Four
specialized SRE agents operate under role-specific tool policy, explicit approval
controls, and objective environment verification.

The current continuation has a genuine bounded QLoRA SFT artifact and a matched
Base-versus-SFT Validation diagnostic. The diagnostic found no improvement. The final
controlled GRPO track ended negatively and produced no acceptable SFT+GRPO checkpoint.

[![Continuation CI](https://github.com/virajchoudhary/AtlasOps/actions/workflows/ci.yml/badge.svg)](https://github.com/virajchoudhary/AtlasOps/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

> [!IMPORTANT]
> **Current continuation status: NOT_CERTIFIED.** G4 remains NOT_PASSED, G9 is frozen
> after a final negative result, and the original G8 live incident-resolution criterion
> remains unmet. The [Master Pipeline inventory](docs/project/MASTER_PIPELINE_STATUS.md)
> is the current gate-status source. Implementation and file hashes do not certify a
> scientific result or live deployment.

**Historical upstream references:** the
[original Hackathon Space](https://huggingface.co/spaces/lablab-ai-amd-developer-hackathon/atlas-ops),
[MI300X narrative](docs/MI300X_EVIDENCE.md), and
[upstream README gap matrix](docs/project/UPSTREAM_README_CURRENT_GAP_MATRIX.md). These
sources document inherited architecture and claims. They do not describe a current
continuation deployment or current team results.

## Current Team Results

| Evidence | Observed result | Interpretation |
|---|---|---|
| Matched Base-vs-SFT Validation diagnostic | Base F1 0.16875; SFT F1 0.15935; paired delta -0.00940; schema 6/6 in both arms | Six alert-only Validation scenarios. No diagnostic improvement observed. |
| SFT v17 | Real QLoRA adapter from 68 synthetic Train-only rows; 9 optimizer steps; fresh-process reload passed | Reloadable bounded artifact. This does not establish incident improvement. |
| Controlled GRPO pilot | Two optimizer steps; four completions; all malformed or blocked with reward -1; both advantage groups zero | No reward-driven GRPO learning established. No acceptable SFT+GRPO checkpoint. |
| Final aligned G9 diagnostic | 0/8 admissible actions; zero optimizer steps; all 392 LoRA tensor hashes unchanged | Negative under the tested parser contract. Zero of eight does not prove the population probability is exactly zero. |
| G4 live incident | 015 inconclusive and unscored; 016 pre-fault abort; 017 and 018 completed negative | G4 remains NOT_PASSED. Attempt 018 is preserved externally; recovery failed and later cleanup verified zero Chaos. |
| Certification | NOT_CERTIFIED | No full-pipeline scientific or deployment certification is claimed. |

The [Base-vs-SFT result](docs/project/BASE_SFT_VALIDATION_RESULT_V1.md) includes
provenance and limitations. The [v17 run record](artifacts/evidence/stage7/free-t4-v17/RESULT.json)
and [independent reload record](artifacts/evidence/stage7/free-t4-v17/reload-v17.json)
identify the artifact. The [final controlled G9 result](docs/project/CONTROLLED_G9_FINAL_NEGATIVE_V1.md)
preserves the negative outcome and its caveats.

## System and Research Scope

The incident workflow retains four agent roles: Triage, Diagnosis, Remediation, and
Comms. The project registers **24 SRE tool wrappers**; role policy exposes **19** to autonomous agents.
Registration does not grant permission. For example,
`chaos_list_experiments` is an exposed read-only tool, while high-risk `kubectl_exec`
is not agent-exposed.

Additional implementation includes:

- Explicit P1 approval. Rejection, timeout, or a missing decision blocks mutation.
- An objective environment verifier that controls recorded resolution.
- Frozen 28-scenario and Train(16)/Validation(6)/Test(6) split governance.
- HMAC-chained audit records and evidence manifests with source and artifact hashes.
- A read-only repository-evidence demo for local review.

The project lead's [current scope revision](docs/project/GAI_RL_SCOPE_REVISION.md)
defines the required academic workstreams.
G10/G11 recommender work remains optional historical research and OUT_OF_SCOPE for
current completion. Historical scenario-derived ranking results do not establish
operator feedback or incident improvement.

## Quick Start

The safe local entry point is the React/TypeScript research console. With Python 3.11+
and Node.js 22+, install the small presentation dependencies and build once:

```powershell
python -m pip install fastapi uvicorn
npm ci --prefix frontend
npm run build --prefix frontend
python -m demo.launcher --host 127.0.0.1 --port 7860
```

Open `http://127.0.0.1:7860/`. The demo browses checked-in evidence on localhost. It
requires no Docker, Kind cluster, GPU, model endpoint, or operational credentials. It
does not load a model or run inference, call kubectl, inject a fault, request approval,
or execute remediation. Its status view is a repository snapshot, not live service
health. The demo API is separate from the operational application and allows only
GET/HEAD requests. See the [Stage 14 demo contract](docs/project/STAGE_14_DEPLOY_FINAL_DEMO.md)
and [component sources](frontend/THIRD_PARTY_NOTICES.md).

Open **Rehearsal** for an interactive scripted incident walkthrough. It pauses for a
simulated P1 decision and demonstrates recovery, rejected/expired approval, and failed
verification. Every output is SYNTHETIC / NON-LIVE / NON-EMPIRICAL, not agent inference
or live incident proof. The [presentation runbook](docs/project/DEMO_RUNBOOK.md) gives
the click path and restart command.

An opt-in local operator mode adds a fixed governed-run launcher, live agent activity
metadata, exact-action approval controls and separate verifier/cleanup results.
It preserves the default read-only mode and rejects dirty source, mismatched profiles,
missing preserved ledger records and exhausted attempt budgets. The current protocol
has used 2/2 slots; the [revised evaluation plan](docs/project/DEMO_REVISED_EVALUATION_PLAN.md)
is prepared but not executed. This software does not establish a successful live demo.

For frontend development, run the Python launcher on port `7862` and
`npm run dev --prefix frontend` in a second terminal. Vite proxies only the
presentation API. Build output and dependencies are local, ignored artifacts.
The existing `requirements/dev-win-py312.lock` remains the Python development
test lock, including historical Gradio regression dependencies; it is not needed
for the new presentation-only launch.

The launcher flags can be inspected without starting the server:

```powershell
& .\.venv\Scripts\python.exe -m demo.launcher --help
```

The separate `infra/local/setup_local.sh --check` preflight is not required for this
demo and does not establish live G3 acceptance. No current safe Space deployment procedure is established.
The historical Space setup notes are retained for provenance, not as deployment instructions.

## Evidence Map

- [Current gate inventory](docs/project/MASTER_PIPELINE_STATUS.md)
- [Technical report](docs/AtlasOps_Technical_Report.md)
- [Presentation source](docs/slides.md)
- [Reviewer guide](JUDGES_START_HERE.md)
- [Evidence index](docs/EVIDENCE_INDEX.md)
- [Deferred research handoff](docs/project/DEFERRED_RESEARCH_HANDOFF.md)
- [v17 SFT evidence](artifacts/evidence/stage7/free-t4-v17/)
- [Base-vs-SFT raw and scored evidence](artifacts/evidence/stage8/base-sft-validation-v1/)
- [Final aligned G9 diagnostic evidence](artifacts/evidence/stage9/final-aligned-diagnostic-v1/)
- [Submission manifest](artifacts/SUBMISSION_MANIFEST.json) and [summary](artifacts/SUBMISSION_SUMMARY.md)

The checked-in submission inventory preserves the pre-redesign package at
`d0e6f4c063e6cc89b8929ce7232c9f9d8b68ccd4`; it is not a hash inventory of the
current React source. `scripts.package_submission.build_submission_package`
can generate a fresh inventory into a separate output directory without replacing
that preserved record. Package tests independently verify both snapshots.

The submission generator hashes selected tracked files. The presentation and
review package are the non-experimental deliverable. The full research pipeline
remains NOT_CERTIFIED. G7 is PASS for the bounded v17 artifact/reload target under
its preapproved free-T4 profile, without any incident-improvement claim.

## Historical Upstream Results

The following figures and images are retained for historical reference only. The
continuation has not reproduced the original MI300X training or live-cluster
benchmark claims. Each image caption identifies that boundary.

The upstream README reported SFT training on 2,028 trajectories and 254 steps, online
GRPO on 236 GKE episodes, and benchmark resolution values of 54%, 68%, and 82% with
rewards of 0.481, 0.601, and 0.729. These are inherited claims, not current team
measurements.

**HISTORICAL UPSTREAM ILLUSTRATION. Not current continuation SFT evidence.**
![Historical upstream SFT loss and token-accuracy illustration](assets/training/sft_loss.png)

**HISTORICAL UPSTREAM ILLUSTRATION. Not a current continuation GRPO run.**
![Historical upstream GRPO reward illustration](assets/training/grpo_reward.png)

**HISTORICAL UPSTREAM ILLUSTRATION. Not a current continuation benchmark.**
![Historical upstream benchmark resolution illustration](assets/training/benchmark_resolution.png)

**HISTORICAL UPSTREAM ILLUSTRATION. Not a current continuation tier evaluation.**
![Historical upstream benchmark-by-tier illustration](assets/training/benchmark_per_tier.png)

The preserved Stage 13 profile outputs are predetermined historical data. Their
100% resolution, 18-second TTR, and 0.918 reward figures are not empirical outcomes.
See [historical benchmark notes](docs/BENCHMARKS.md) and
[the current pipeline status](docs/project/MASTER_PIPELINE_STATUS.md) for the
distinction between inherited claims and current evidence.

## Training Boundary

Training entrypoints are separate from the read-only demo. Stage 7 and Stage 9
contracts record pinned `--model-revision` and `--tokenizer-revision` requirements for
their governed paths. Live GRPO rollout execution is serial. The final controlled G9
track is frozen; this README gives no training or evaluation launch command.

The [Stage 7 contract](docs/project/STAGE_7_SFT_DATA_AND_TRAINING.md) and
[Stage 9 contract](docs/project/STAGE_9_ONLINE_GRPO.md) are reference documents, not
authorization to train, run inference, access Test or Leaderboard outcomes, mutate a
cluster, or use paid compute.

## License and Attribution

This fork preserves the upstream Git history, MIT license, and attribution. The
upstream repository was frozen at
[`bf9bd197c9f4a05ae55ade254802a9eef1a74356`](https://github.com/Harikishanth/AtlasOps/commit/bf9bd197c9f4a05ae55ade254802a9eef1a74356).
See [LICENSE](LICENSE) and the [project operating contract](AGENTS.md).
