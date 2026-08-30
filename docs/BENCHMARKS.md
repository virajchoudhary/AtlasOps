# AtlasOps — Benchmark Results

> [!WARNING]
> **Inherited upstream artifact — not reproduced by this team.**
> Every figure below was produced by the original authors of
> [Harikishanth/AtlasOps](https://github.com/Harikishanth/AtlasOps) on hardware this team
> has not run. None of it has been reproduced here, and several defects found since —
> an unobservable benchmark goal state, a GRPO reward that could never award resolution,
> an uncoupled policy gradient, a judge outage that scored better than a working judge,
> and `scenario_id` leaking into the model-visible prompt — mean these numbers cannot be
> read as measurements of the current system. Retained for provenance only.
> Current verified status: [`docs/project/MASTER_PIPELINE_STATUS.md`](project/MASTER_PIPELINE_STATUS.md).


> Historical upstream result record. The continuation team has not yet reproduced
> these live-cluster benchmark results. The active static catalogue contract is 28
> frozen scenarios; the runner separately requests up to 10 dynamically generated
> adversarial scenarios by default.

| Tag | Model | Resolution | Reward (Judge) | Reward (Contract) | Avg Penalty | Avg Turns | Cascade Res. | Replay Res. | Date |
|---|---|---|---|---|---|---|---|---|---|
| baseline_zero_shot | `Qwen2.5-7B-Instruct` | 54% | 0.481 | 0.439 | 0.092 | 8.3 | 40% | 30% | 2026-05-09 |
| grpo_v3 | `grpo_v3` | 82% | 0.729 | 0.712 | 0.031 | 6.1 | 78% | 72% | 2026-05-09 |

## Per-tier Breakdown

### baseline_zero_shot
| Tier | Count | Resolution | Avg TTR (s) | Avg Contract Reward |
|---|---|---|---|---|
| cascade | 5 | 40% | 148.6 | 0.382 |
| multi_fault | 5 | 40% | 163.2 | 0.361 |
| named_replays | 10 | 30% | 139.8 | 0.398 |
| single_fault | 8 | 63% | 72.1 | 0.521 |

- unsafe actions: `5`, false resolutions: `4`, hallucinated evidence: `6`

### grpo_v3
| Tier | Count | Resolution | Avg TTR (s) | Avg Contract Reward |
|---|---|---|---|---|
| cascade | 5 | 78% | 67.4 | 0.701 |
| multi_fault | 5 | 76% | 74.1 | 0.688 |
| named_replays | 10 | 72% | 61.8 | 0.706 |
| single_fault | 8 | 88% | 41.3 | 0.764 |

- unsafe actions: `1`, false resolutions: `1`, hallucinated evidence: `2`

---

## Summary: +28pp Improvement (Zero-shot → GRPO on AMD MI300X)

| Metric | Zero-shot baseline | AtlasOps GRPO (MI300X) | Delta |
|---|---|---|---|
| Resolution rate | 54% | **82%** | **+28 pp** |
| Avg reward (judge) | 0.481 | **0.729** | **+0.248** |
| avg_reward_contract | 0.439 | **0.712** | +0.273 |
| avg_penalty | 0.092 | **0.031** | −66% |
| Avg turns | 8.3 | **6.1** | −26% |
| Cascade resolution | 40% | **78%** | +38 pp |
| Named replay resolution | 30% | **72%** | +42 pp |
| unsafe_actions | 5 | **1** | −80% |
| false_resolution | 4 | **1** | −75% |
| hallucinated_evidence | 6 | **2** | −67% |

**Training hardware:** AMD MI300X (192 GB HBM3), ROCm 7.2, vLLM 0.17.1
**Training recipe:** QLoRA SFT (4-bit NF4, LoRA r=16) → Online GRPO (DAPO loss, G=8 rollouts)
**Scenarios in this historical result:** 28 frozen (8 single-fault + 5 cascade + 5 multi-fault + 10 named replays)
**Anti-gaming:** reward contract penalises command spam, false resolution, hallucinated evidence

> Upstream reports that these live results were produced on a real GKE cluster
> (us-central1) with Prometheus/Jaeger/Argo CD APIs. This has not been reproduced
> by the continuation team.
> Reproduce: `python bench/runner.py --model checkpoints/grpo_v3 --tag grpo_v3`
