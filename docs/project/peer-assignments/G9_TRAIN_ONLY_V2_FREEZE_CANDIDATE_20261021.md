# G9 Train-only v2 — freeze sheet CANDIDATE, not approved

**Status:** prepared offline on 21 October 2026 for project-lead review. It binds the
existing [`G9-TRAIN-OBSERVATION-ACTION-V2-PROPOSED` contract](../PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md)
to concrete identities found in source and lists what is still missing. It does
not reopen G9, change `training/grpo_controlled.py`, or authorize inference or training.
G9 remains **NOT_PASSED / FROZEN**. Owner values are `UNSPECIFIED`.

## Identities read from source `b23d6cb4319b77b4d8606934e401141bcb01d23f`

| Field | Value | Source |
| --- | --- | --- |
| Base model / tokenizer | `Qwen/Qwen2.5-7B-Instruct` @ `a09a35458c702b33eeacc393d103063234e8bc28` | `training/grpo_controlled.py` `MODEL`, `REVISION` |
| SFT parent (v17) manifest SHA-256 | `7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440` | `PARENT_MANIFEST_SHA256` |
| SFT parent (v17) weight SHA-256 | `f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff` | `PARENT_WEIGHT_SHA256` |
| Scenario | `single_fault/sf-002`, Train split only (`begin()` rejects anything else) | `SCENARIO` |
| Trainable parameters | v17 LoRA parameters only | `"Only the v17 LoRA parameters may train"` guard |
| Pinned runtime | torch 2.7.1, trl 0.19.1, transformers 4.57.6, peft 0.17.1, accelerate 1.14.0, datasets 4.8.5, tokenizers 0.22.2, huggingface-hub 0.36.2 | `.github/workflows/ci.yml` trainer job |

## v2 values vs the installed (frozen v1) profile

| Setting | Installed `controlled-g9-capacity-v1` | v2 proposal | Freeze decision |
| --- | --- | --- | --- |
| Groups × completions | 2 steps × `num_generations=2` | 2 groups × 4 completions | `UNSPECIFIED` — needs a new reviewed profile; v1 must not be edited |
| Max new tokens | 192 | ≤192 | 192 proposed |
| Optimizer steps | 2 | ≤2 | 2 proposed |
| Seed | 2026 | 2026 | 2026 proposed |
| beta (KL) | 0.04 | must be frozen; reward-only diagnostic runs at beta 0 | `UNSPECIFIED` |
| Learning rate, temperature, top_p, top_k, batch | 1e-6, 1.0, 1.0, 50, 2 | not yet frozen | `UNSPECIFIED` |
| `REWARD_GRADIENT_DIAGNOSTIC_AVAILABLE` | `False` | must remain until reviewed | unchanged |

## Learning-evidence acceptance (all required, independently recomputed)

Two admissible same-group actions with different verifier-derived
`r_verified_resolution` or `r_required_check_coverage`; nonzero centered advantages;
a finite, strictly positive environmental-only policy gradient; an attributable LoRA
update (before/after tensor hashes) not explained by KL alone; full checkpoint inventory;
fresh-process offline reload (Linux x86_64 seccomp guard). Claim-only, syntax-only,
KL-only, invalid-penalty-only and first-group-rescue variation must still fail —
the 21 Oct 2026 pinned runs on this branch's record confirm those rejections on tiny fixtures only.

## Stop rules

Stop in the first group on malformed bindings, missing reward-only diagnostic, verifier
failure, no admissible action or no environmental reward variation; stop on nonfinite
gradients, unchanged parameters, source drift, expired authority or uncertain output
ownership. Preserve every raw failure; accepted checkpoint stays null; no retry or budget extension.

## Owner-supplied before any launch

| Item | Owner | Value |
| --- | --- | --- |
| G9 reopening and prerequisite exception (normally G4, G6, G8 first) | Project lead | `UNSPECIFIED` |
| CUDA host with measured VRAM ≥ proposal (24 GiB) and peak-memory fit | Host owner | `UNSPECIFIED` |
| Training/model/compute authorization, wall-clock ceiling | Project lead | `UNSPECIFIED` |
| v17 tokenizer/chat-template direct-action compatibility proof | Maintainer + reviewer | `UNSPECIFIED` |
| Independent adjudicator | Reviewer | `UNSPECIFIED` |

This Apple M4 laptop (no CUDA, 16 GiB unified memory) does not qualify.
