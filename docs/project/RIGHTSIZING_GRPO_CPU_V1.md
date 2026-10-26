# Right-Sizing GRPO on CPU (v1): Small-Model Experiment

25 October 2026. Classification: `SMALL_MODEL_CPU_RL_EXPERIMENT`.

This is not G9 evidence and not a G9 retry. G9 remains NOT_PASSED/FROZEN, and
[the final negative result](CONTROLLED_G9_FINAL_NEGATIVE_V1.md) still says no further
G9 training retries are authorized. This experiment uses a different model, task,
hardware and protocol, touches no G9 artifact, and changes no gate. It was run because
the person driving this session asked for the RL training to be done in it. Nobody has
reviewed it independently.

## Result in brief

| Arm | Held-out states solved (of 64) | Valid actions |
|---|---|---|
| Base model, same reasoned prompt | 3 | 64 |
| SFT on 16 demonstrations (the GRPO start) | 7 | 34 |
| **After 100 GRPO steps** | **28** | **64** |
| SFT on 64 demonstrations, no RL | 64 | 64 |

- **GRPO improved the policy.** Same model, same states: 26 gained and 5 lost (exact
  McNemar p = 0.0002), and mean verifier-scored reward rose from −0.289 to 0.531.
- **But it only half-learned the task.** The trained policy answers
  `floor(demand / capacity)` on 57 of 64 states. That solves 24 of 25 states where the
  division is exact but only 4 of 39 where it is not. Every miss is off by one replica.
- **RL was not the best tool here.** SFT on 64 demonstrations solves every state, while
  GRPO from 16 demonstrations reached 28.
- **The one-shot format failed.** When the model had to answer in one shot, GRPO made the
  SFT policy slightly worse: 14 → 10 solved, 4 lost and none gained, p = 0.125.
- **Follow-ups (v1.1).** Two pre-registered runs both improved on 28 (see
  [Follow-up v1.1](#follow-up-v11-pre-registered-before-running-26-october-2026)), but
  neither met the "round-down fixed" criterion.
  - **Longer training, repository reward: 40 solved.** It gained 12 states and lost none,
    but still rounds down on 23.
  - **Graded reward: 42 solved.** It now over-provisions instead.

## Why a separate experiment

The governed G9 path needs:

- the pinned Qwen2.5-7B-Instruct on CUDA;
- a reopening by the project lead;
- a resource permit.

This session had none of those: 4 CPU cores, no GPU, and no route to huggingface.co. What
it could do was run reinforcement learning end to end at small scale, using the
repository's own action parser, schema validation, tool policy and verifier-scored reward.
Every result is kept, including the negative ones.

## Setup

### Model

Qwen2.5-0.5B-Instruct: 494.03 M parameters, Apache-2.0.

- **Source.** Docker Hub's OCI model registry, `registry-1.docker.io/ai/qwen2.5:0.5B-F16`,
  because huggingface.co is not reachable here.
  - Manifest digest: `sha256:3e1aad67b4cc8e3dca660fe65f9f73edb598474284256ffdd9ba460b5b35ff26`.
    The runs' `RESULT.json` files record `a78aecb6…`, which is the SHA-256 of a saved copy
    with one trailing newline added. The JSON content is identical.
  - GGUF blob: `sha256:f1ad9d1174ce6ab47b584d522634c47e411b75bffdffd9a4e106e21e882392e5`.
- **Conversion.** The transformers 4.57.6 GGUF loader converted it to float32 safetensors.
  `model.safetensors` SHA-256:
  `dbad9b575e2e3ea20f10ef031b58692fc7b796470d89a06018a621c1a4e93832`.
- **Tokenizer fix.** The converted tokenizer ended turns with `<|endoftext|>`. It was set
  to `<|im_end|>`, as in upstream Qwen2.5-Instruct.
  - `grpo-direct-from-base` ran before this fix; every later run ran after it.
  - Evaluation stops at either token explicitly.
- **Repeat script.** [`scripts/fetch_qwen_oci_model.py`](../../scripts/fetch_qwen_oci_model.py)
  repeats the fetch, digest checks, conversion and fix.
  - Its conversion step, run on the saved blobs, gave byte-identical model and tokenizer
    files.
  - Its download step was not repeated end to end: Docker Hub returned HTTP 429 (rate
    limit) to the re-fetch.

### Runtime

- Linux x86_64, 4 CPUs, Python 3.12.3.
- torch 2.7.1 (CPU, float32), transformers 4.57.6, TRL 0.19.1, PEFT 0.17.1.

### Environment

[`training/rightsizing_env.py`](../../training/rightsizing_env.py) generalises the
controlled-G9 `CapacityEnvironment`:

- Each episode has its own ready replica count, demand and capacity per replica.
- The verifier has three required checks:
  - capacity covers demand;
  - ready capacity is positive;
  - `right_sized`: no more replicas than needed.
- Only `ceil(demand / capacity)` resolves.
- 990 states (capacity 1–5, needed 2–12) are split with seed 2026 into:
  - 926 Train states, SHA-256 `6b8f6389…64e8`;
  - 64 held-out states, SHA-256 `e2b24ce0…950a`. In 25 of them, demand divides
    exactly by capacity.

Actions pass the G9 environment's parser, `_validate_schema`, `_check_tool_policy`
(replicas 0–20) and `score_direct_action_step`:

| Outcome | Reward |
|---|---|
| Resolved | 1.0 |
| Admissible but wrong | 0.25 × check coverage, which is 0.1667 for every over- or under-provisioned count |
| Invalid or blocked | −1 |

The policy's `agent_claimed_resolved` is recorded but, as in the controlled-G9
environment, never changes the reward.

### Action formats

- `direct`: one JSON action.
- `reasoned`: one arithmetic line, then the JSON action. Only the last line is scored,
  at most four lines in total, and the arithmetic itself earns nothing.

### Training

- **GRPO:** TRL `GRPOTrainer` with LoRA r=16, alpha=32, on all seven projection matrices.
  - beta=0, temperature 1.0, learning rate 5e-5.
  - Eight generations per group, one prompt per step.
  - 40% of prompts come from easy states (capacity ≤ 2).
- **SFT:** LoRA warm start on environment-computed correct actions for Train states only,
  with loss on assistant tokens only.

### Evaluation

- Greedy decoding on the 64 held-out states and on a fixed 64-state Train sample.
- Each GRPO adapter is saved with a SHA-256 inventory and re-evaluated in a fresh process.
  That evaluation must reproduce the in-process completions exactly, and both GRPO
  adapters that were saved did.

Code: [`scripts/train_rightsizing_grpo.py`](../../scripts/train_rightsizing_grpo.py). Every run
started from a clean commit.

## Runs

Run names are the evidence directory names.

| Run | Commit | Start policy | Budget | Wall time | Held-out solved | Held-out reward | Train sample solved |
|---|---|---|---|---|---|---|---|
| Base, direct prompt | 10e86ae | — | — | — | 1/64 | 0.180 | 1/64 |
| `grpo-direct-from-base` | 10e86ae | base | stopped at 11 of 150 steps | 6 min | not evaluated | — | — |
| `sft-direct-64` | 443578b | base | 64 demos, 2 epochs, 16 steps | 346 s | 14/64 | 0.349 | 12/64 |
| `grpo-direct-from-sft` | 443578b | `sft-direct-64` | 150 steps | 73 min | 10/64 | 0.297 | 10/64 |
| Base, reasoned prompt | 35f764d | — | — | — | 3/64 | 0.206 | 2/64 |
| `sft-reasoned-64-killed` | 9897afe | base | 64 demos, 2 epochs | killed | not evaluated | — | — |
| `sft-reasoned-64` | 35f764d | base | 64 demos, 2 epochs, 16 steps | 305 s | 64/64 | 1.000 | 62/64 |
| `sft-reasoned-8` | 35f764d | base | 8 demos, 2 epochs, 2 steps | 40 s | 0/64, none valid | −1.000 | 0/64 |
| `sft-reasoned-16` | 35f764d | base | 16 demos, 2 epochs, 4 steps | 79 s | 7/64, 34 valid | −0.289 | 10/64 |
| `grpo-reasoned-from-sft16` | 35f764d | `sft-reasoned-16` | 100 steps | 48 min | 28/64 | 0.531 | 29/64 |
| `grpo-reasoned-graded-from-sft16` (v1.1) | f7fcc46 | `sft-reasoned-16` | 100 steps, graded reward | 50 min | 42/64 | 0.714 | 42/64 |
| `grpo-reasoned-continued` (v1.1) | f7fcc46 | `grpo-reasoned-from-sft16` | 150 more steps | 78 min | 40/64 | 0.688 | 39/64, different sample (seed 2027) |

- GRPO held-out numbers are from the fresh-process reload.
  - The `grpo-reasoned-from-sft16` reload ran from a clean tree at `529c688`. That commit
    adds only a model-fetch script and a `reload_source` field to the record.
- Each GRPO run's start-policy evaluation reproduces its SFT run's held-out completions
  exactly.
- All 336 LoRA tensors changed in both full GRPO runs.

## What happened

1. **The base model copies the current replica count.** With the direct prompt, 62 of 64
   held-out answers repeat `ready_replicas`.
2. **GRPO from the base model had nothing to learn from** (`grpo-direct-from-base`).
   - Of 88 sampled actions in 11 steps, none was correct.
   - 59 repeated the current count and 9 were invalid, so groups differed only by
     malformed JSON.
   - The run was stopped and the SFT warm start added. The commit message of `443578b`
     says 58 repeated; recounting from `rollouts.jsonl` gives 59.
3. **In the direct format, GRPO made the SFT policy worse** (`grpo-direct-from-sft`).
   - Held-out solved fell from 14 to 10: 4 lost, none gained.
   - Of 150 groups, 103 gave eight identical wrong rewards, 27 eight identical correct
     ones, and only 20 varied.
   - The policy over-provisioned by 1–10 replicas. A 0.5B model answering in one shot was
     not computing demand ÷ capacity.
   - The reward gives the same 0.1667 whether an answer is off by one or by ten, so it
     carries no "closer" signal. A zero-variance group gives no GRPO gradient.
4. **Let the model show its arithmetic** (`sft-reasoned-64`).
   - With 64 reasoned demonstrations, SFT alone solves all 64 held-out states, and every
     arithmetic line is right. RL has nothing left to add there, so GRPO was not run from
     that adapter.
   - The first attempt, `sft-reasoned-64-killed`, was killed by the kernel at about 14 GB
     resident memory. Micro-batch gradient accumulation (`35f764d`) fixed that.
5. **A weaker warm start, so RL has room to work** (`sft-reasoned-8`, `sft-reasoned-16`).
   - The rule, stated in the session before running: SFT on 8 reasoned demonstrations,
     then GRPO only if the Train sample lands between 10% and 80% solved. Held-out results
     were not to be used for that decision.
   - 8 demonstrations broke the output format (0/64 valid). The next try, 16
     demonstrations, gave 10/64 on the Train sample, so it became the GRPO start policy.
   - Choosing 16 after seeing 8 fail was a decision made after a result. It is reported
     here as one.
6. **GRPO from the 16-demonstration adapter** (`grpo-reasoned-from-sft16`).
   - Validity came first.
     - In steps 1–10, 22 of 80 samples were invalid and 10 had more than one line.
     - From step 11 on, no sample had an arithmetic line. The policy switched to a bare
       JSON answer, which the format allows and the reward scores the same.
     - Samples solved per 10 steps rose from 4/80 to 61/80 by steps 31–40, then varied
       between 25 and 44 out of 80.
     - 38 of 100 groups had varying rewards.
   - On held-out states the trained policy is always valid and answers
     `floor(demand / capacity)` on 57 of 64. It solves 24 of 25 exact-division states and
     4 of 39 others. Its 36 misses are 33 one replica short and 3 one over.
   - In other words, it learned integer division but not rounding up. The reward does not
     separate a one-replica miss from a ten-replica one, and both under- and
     over-provisioning earn 0.1667, so nothing pushed it from floor to ceiling.

## Paired comparisons on the same held-out states

| From → to | Solved | Gained | Lost | Exact McNemar p |
|---|---|---|---|---|
| Base direct → `sft-direct-64` | 1 → 14 | 14 | 1 | 0.001 |
| `sft-direct-64` → `grpo-direct-from-sft` | 14 → 10 | 0 | 4 | 0.125 |
| Base reasoned → `sft-reasoned-16` | 3 → 7 | 7 | 3 | 0.344 |
| `sft-reasoned-16` → `grpo-reasoned-from-sft16` | 7 → 28 | 26 | 5 | 0.0002 |

Of the 26 states GRPO gained, 13 started as invalid actions and 13 as valid but wrong
answers. Every comparison above is recomputed from the committed files by
[`scripts/analyze_rightsizing_runs.py`](../../scripts/analyze_rightsizing_runs.py).
[`tests/test_rightsizing_evidence.py`](../../tests/test_rightsizing_evidence.py) pins the
quoted numbers.

## Limitations

- **Not G9.** The model, task, hardware and protocol all differ. No project-lead
  authorization or independent review exists, and the result changes no gate.
- **The held-out set was not single-use.**
  - Every arm was scored on the same 64 states, and those results were seen as the session
    went on.
  - Branch decisions used Train-sample results, but this is not a pre-registered study.
  - Each arm has one seed, and the p-values are uncorrected for the four comparisons.
- **The task is synthetic arithmetic, not incident diagnosis.** No cluster, Chaos Mesh or
  live service is involved.
- **The reward cannot tell near misses from far ones.** A distance-aware reward or longer
  training might reach `ceil`. Neither was tried, so that the repository's reward stayed
  unchanged and the run count stayed bounded.
- **Changed LoRA tensors are not evidence of learning.** The held-out change is the
  signal, and at n=64 it is coarse.
- **The adapters (35 MB each) are not in Git,** under AGENTS.md. Their SHA-256
  inventories are in each `RESULT.json`.
- **The graded reward is not the repository reward.** The graded-reward arm's result
  describes that reward's design, not `score_direct_action_step`.

## Evidence

[`artifacts/evidence/rl-cpu-rightsizing-v1/`](../../artifacts/evidence/rl-cpu-rightsizing-v1/SHA256SUMS)
holds one directory per run, each with:

- `RESULT.json`: source commit, runtime, model inventory and provenance, data hashes,
  config, metrics and adapter inventory;
- `training_log.jsonl`: per-step metrics;
- `rollouts.jsonl`: every GRPO rollout with its reward;
- `*_held_out.jsonl`: every scored held-out completion;
- the run's console output.

`SHA256SUMS` covers every file. Trainer checkpoints and adapters are left out, and so is a
two-step plumbing smoke run from a dirty tree.

## Reproduction

The output directories must be outside the repository. Run these from the repository
root, with the pinned trainer stack installed (TRL 0.19.1, PEFT 0.17.1, transformers
4.57.6, torch 2.7.1, plus `gguf`):

```bash
M=/path/outside/repo
python -m scripts.fetch_qwen_oci_model --download-dir $M/gguf --output-dir $M/qwen2.5-0.5b-instruct-hf
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 CUDA_VISIBLE_DEVICES=""
Q=$M/qwen2.5-0.5b-instruct-hf
# Direct format: SFT, then GRPO, then the fresh-process evaluation.
python -m scripts.train_rightsizing_grpo sft --model-dir $Q --output-dir $M/sft-direct-64
python -m scripts.train_rightsizing_grpo train --model-dir $Q --output-dir $M/grpo-direct-from-sft \
    --max-steps 150 --init-adapter $M/sft-direct-64/adapter
python -m scripts.train_rightsizing_grpo evaluate --model-dir $Q --run-dir $M/grpo-direct-from-sft
# Reasoned format from 16 demonstrations.
python -m scripts.train_rightsizing_grpo sft --model-dir $Q --output-dir $M/sft-reasoned-16 \
    --examples 16 --action-format reasoned
python -m scripts.train_rightsizing_grpo train --model-dir $Q --output-dir $M/grpo-reasoned-from-sft16 \
    --max-steps 100 --action-format reasoned --max-completion-length 96 \
    --init-adapter $M/sft-reasoned-16/adapter
python -m scripts.train_rightsizing_grpo evaluate --model-dir $Q --run-dir $M/grpo-reasoned-from-sft16
# v1.1 follow-ups: the graded reward, and 150 more steps of the adapter above.
python -m scripts.train_rightsizing_grpo train --model-dir $Q --output-dir $M/grpo-reasoned-graded-from-sft16 \
    --max-steps 100 --action-format reasoned --max-completion-length 96 --reward graded \
    --init-adapter $M/sft-reasoned-16/adapter
python -m scripts.train_rightsizing_grpo train --model-dir $Q --output-dir $M/grpo-reasoned-continued \
    --max-steps 150 --action-format reasoned --max-completion-length 96 --seed 2027 --easy-fraction 0.0 \
    --init-adapter $M/grpo-reasoned-from-sft16/adapter
python -m scripts.train_rightsizing_grpo evaluate --model-dir $Q --run-dir $M/grpo-reasoned-graded-from-sft16
python -m scripts.train_rightsizing_grpo evaluate --model-dir $Q --run-dir $M/grpo-reasoned-continued
# Recompute the comparisons from the committed evidence.
python -m scripts.analyze_rightsizing_runs
```

Sampling on CPU is seeded but has not been shown to be bit-reproducible across machines.

## Follow-up v1.1: pre-registered before running (26 October 2026)

Goal: test whether the round-down failure of `grpo-reasoned-from-sft16` can be fixed. This
section was committed before either run started. Each run's `RESULT.json` records a
source commit that contains it.

Training samples from that run show the correct rounded-up answer did appear on
inexact-division prompts, and more often over time: 3 of 88 samples in steps 1–34, 26 of
168 in steps 35–67, 40 of 168 in steps 68–100. Floor answers still dominated, with 81 of
168 in the last third. So two arms are run, and no others:

| Arm | Start policy | Training reward | Steps | Seed | Easy fraction | Everything else |
|---|---|---|---|---|---|---|
| `grpo-reasoned-graded-from-sft16` | `sft-reasoned-16` adapter | `graded` | 100 | 2026 | 0.4 | as `grpo-reasoned-from-sft16` |
| `grpo-reasoned-continued` | `grpo-reasoned-from-sft16` adapter | repository | 150 more | 2027 | 0.0 | as `grpo-reasoned-from-sft16` |

**The two arms.**

- **Graded reward.** This arm changes only the training reward, so it sees the same
  prompts as `grpo-reasoned-from-sft16`.
  - The `graded` reward (`training/rightsizing_env.py`) keeps 1.0 for a verified
    resolution and −1 for an invalid or blocked action.
  - A count that covers demand but over-provisions earns 0.5 × needed / replicas.
  - A count that falls short earns 0.25 × replicas / needed.
  - So one replica too many always earns more than one too few. It is an experiment-only
    reward, not the G9 reward.
- **Longer training.** This arm keeps the repository reward and continues the trained
  adapter.
  - It uses a fresh prompt order and no easy-first phase, because the start policy is
    already past it.
  - Its start-policy evaluation must reproduce the 64 held-out completions of
    `grpo-reasoned-from-sft16`; otherwise the run is reported as invalid.

**Outcome and criterion.**

- **Primary outcome:** held-out states solved, overall and on the 39 inexact-division
  states. Each arm is paired against `grpo-reasoned-from-sft16` with an exact McNemar test.
- **Evaluation:** every evaluation uses the repository reward and the fresh-process
  reload.
- **"Round-down fixed"** means both of these hold:
  - at least 20 of 39 inexact-division states solved;
  - at least 22 of 25 exact-division states solved.
- **Rules:** both arms are reported whatever they show. No setting changes after seeing
  results, and no further arms run in this round.

### Results (26 October 2026)

Both runs started from clean commit `f7fcc46`. The pre-registration above was committed
earlier, in `56d8251`. For each run:

- the start-policy evaluation reproduced its named starting point's 64 held-out
  completions exactly;
- the fresh-process reload reproduced its in-process completions exactly;
- all 336 LoRA tensors changed.

| Arm | Solved (of 64) | Exact-division (≥ 22 of 25 needed) | Inexact-division (≥ 20 of 39 needed) | Criterion met | Paired against `grpo-reasoned-from-sft16` |
|---|---|---|---|---|---|
| `grpo-reasoned-from-sft16` (reference) | 28 | 24 | 4 | — | — |
| `grpo-reasoned-graded-from-sft16` | 42 | **17** | 25 | **No** | 21 gained, 7 lost, p = 0.0125 |
| `grpo-reasoned-continued` | 40 | 24 | **16** | **No** | 12 gained, 0 lost, p = 0.0005 |

- **Graded reward.** It fixed the rounding but overshot.
  - On inexact-division states it gained 21 and lost none (exact McNemar p < 0.0001).
  - On exact-division states it lost 7, because it now adds a replica it does not need.
  - Its 22 misses are 8 one over, 11 two over, 1 three over and 2 one short.
  - Rewarding "covers demand" well above "falls short" pushed the policy past the target.
  - 61 of its 100 groups had varying rewards, against 38 in the reference run.
- **Longer training.** It improved without new errors.
  - All 12 gains were inexact-division states. Exact-division stayed at 24 of 25.
  - It still rounds down: its 24 misses are 23 one short and 1 one over.
  - 45 of its 150 groups had varying rewards.
- **Reading.** The round-down was partly a training-length effect: more steps with the
  repository reward fixed a third of the inexact misses at no cost. The graded reward's
  extra credit for covering demand fixed more of them but caused over-provisioning.
  Neither run solves the task. SFT on 64 reasoned demonstrations still does (64/64).

The evidence directory also holds `grpo-reasoned-graded-from-sft16/` and
`grpo-reasoned-continued/`. `scripts/analyze_rightsizing_runs.py` recomputes the table,
and `tests/test_rightsizing_evidence.py` pins it.

## Follow-up v1.2: aiming for a good result, pre-registered (26 October 2026)

This section was committed before any v1.2 run started. Whatever v1.2 shows, it changes no
gate. G9 is defined on the pinned Qwen2.5-7B, a CUDA host and the controlled incident
environment, and it needs a reopening and independent review. This is a 0.5B model on
CPU on a synthetic task.

**Why another round.**

- No run so far rounds up reliably.
- In the `reasoned` format a bare action also counts, and every GRPO policy learned to
  drop the arithmetic line. With that line, SFT alone gets 64/64.
- The new `reasoned_strict` format scores only the two lines the prompt asks for: an
  arithmetic line in the demonstrated form, then the action. Anything else is an invalid
  action (−1). The arithmetic's correctness earns nothing, and only the action is verified.
- The reward is the repository reward.

**Fresh evaluation sets.** The 64 held-out states were scored by every arm, so v1.2 does
not use them for decisions.

- [`scripts/make_rightsizing_eval_sets.py`](../../scripts/make_rightsizing_eval_sets.py)
  draws two sets with seed 31415, written to
  [`eval-sets-v1.2.json`](../../artifacts/evidence/rl-cpu-rightsizing-v1/eval-sets-v1.2.json).
  - Both come from the 619 Train states that no earlier run touched as a prompt,
    demonstration or evaluation sample.
  - **Development:** 64 states, 20 of them exact divisions.
  - **Confirmation:** 128 states, 37 of them exact divisions.
- Both sets are excluded from every v1.2 training prompt (`train --exclude-states`).

**Candidates.** Run in this order, stopping at the first that meets the development
target. Unless stated, everything else is as in `grpo-reasoned-from-sft16`.

| # | Run | Start | Changes |
|---|---|---|---|
| C1 | `grpo-strict-from-sft16` | `sft-reasoned-16` adapter | `reasoned_strict`, 150 steps |
| C2 | `grpo-strict-continued` | C1's adapter | `reasoned_strict`, 150 more steps, seed 2027, no easy-first phase |
| C3 | `grpo-strict-2prompts` | `sft-reasoned-16` adapter | `reasoned_strict`, 2 prompts per step, 150 steps |

**Development target.** At least 58 of 64 solved, at least 17 of 20 exact-division
states and at least 38 of 44 inexact-division states.

**Confirmation.**

- The first candidate that meets the development target is scored once on the
  confirmation set.
- Its start policy (`sft-reasoned-16`) and two references (`grpo-reasoned-continued` and
  `sft-reasoned-64`) are scored on the same set in the strict format.
- The result counts as good if both of these hold:
  - at least 116 of 128 solved;
  - an exact McNemar p below 0.01 against the start policy on the same states.

**Rules.**

- Every candidate that runs is reported, whatever it shows.
- If none of C1–C3 meets the development target, v1.2 stops and reports that, with no
  further variants.
- The original held-out set is still reported for continuity, marked as reused.

### Amendment 1 (26 October 2026, before any development or confirmation scoring)

- **C1 stopped.** `grpo-strict-from-sft16` was stopped at step 12 of 150.
  - 95 of its 96 samples were invalid under `reasoned_strict`, and only 1 was solved.
  - At temperature 1.0, the 16-demonstration policy wrote a bare action 72 times out of
    96. Most of its other outputs were malformed arithmetic.
  - Groups that all score −1 give GRPO no gradient. The run is kept as a stopped negative
    result, like the first direct-format run.
  - No v1.2 policy had yet been scored on the development or confirmation set, so stopping
    used no evaluation-set information.
- **C2 dropped.** C2 would have continued C1, so it is dropped.
- **C3 replaced.** C3 started from the same policy, so it is replaced by the plan below.

**New start policy.** SFT in the `reasoned_strict` format, decided on the Train evaluation
sample only. Demonstrations exclude both v1.2 sets (`sft --exclude-states`).

- **S32:** 32 demonstrations, 2 epochs, otherwise as `sft-reasoned-16`.
- S32 qualifies if its Train sample is at least 75% valid and between 20% and 85% solved.
- If it solves more than 85%, try **S24** (24 demonstrations); if less than 20%, or less
  than 75% valid, try **S48** (48 demonstrations).
- At most these two SFT runs. If neither qualifies, v1.2 stops.

**New candidates.** All start from the qualifying SFT policy, in this order. The
development target, confirmation rule and stop rule are unchanged; the confirmation's
start-policy comparison uses this SFT policy.

| # | Run | Changes |
|---|---|---|
| D1 | `grpo-strict-from-sft` | `reasoned_strict`, 150 steps |
| D2 | `grpo-strict-continued` | D1's adapter, 150 more steps, seed 2027, no easy-first phase |
| D3 | `grpo-strict-2prompts` | `reasoned_strict`, 2 prompts per step, 150 steps |

A good result from a stronger start shows RL's contribution only as the gain over that
start. The confirmation's paired test against the start policy measures exactly that.

### Amendment 2 (26 October 2026, before any development or confirmation scoring)

Neither start policy qualified (Train evaluation sample, greedy, `reasoned_strict`):

| SFT | Valid | Solved | Qualifies (≥ 75% valid, 20–85% solved) |
|---|---|---|---|
| S32 | 37/64 (58%) | 15/64 (23%) | No: too few valid |
| S48 | 64/64 (100%) | 61/64 (95%) | No: too good for RL to show a gain |

Amendment 1's rule says v1.2 stops here. The person running this session asked to keep
going until a good result is reached, so the search continues under the same constraint:
every decision uses only the Train evaluation sample. The development and confirmation
sets are still unscored.

- **S40** (40 demonstrations). If it qualifies, D1–D3 start from it.
- If S40 is too good, try **S36**. If it has too few valid answers or solves too few, try
  **S44**.
- At most these two further SFT runs. If neither qualifies, D1–D3 start from S48 instead.
  S48 leaves little room, so any confirmed gain over it would be small, and the report
  must say so.

### Amendment 3 (26 October 2026, before any development or confirmation scoring)

S40 qualified on its Train evaluation sample: 49/64 valid (77%) and 27/64 solved (42%). D1
(`grpo-strict-from-sft`) started from it and was stopped at step 39 of 150.

**What the training samples showed.**

| Steps | Exact-division prompts | Exact solved | Inexact solved | Inexact arithmetic line with the floor as "rounded up" |
|---|---|---|---|---|
| 1–15 | 67% | 39/80 | 12/40 | 13/40 |
| 16–39 | 62% | 118/120 | 0/72 | 71/72 |

The policy kept the arithmetic line but wrote the floor as "rounded up", for example
"17 / 2 = 8.50, rounded up = 8".

**Diagnosis: the easy-first curriculum, not the reward.**

- The first 40% of prompts come from capacity ≤ 2 states, and 69% of those divide exactly.
  Only 34% of the full prompt pool does.
- On exact prompts the start policy's typical error is adding a replica. RL corrected that
  into "never add one", which then generalised to inexact prompts.
- Once a policy always answers the floor, inexact groups score identically and stop giving
  a gradient. The same curriculum ran in every earlier GRPO run, which would also explain
  `grpo-reasoned-from-sft16` rounding down.

**Changes.** D2 continued D1, so it is dropped. D3 shared the curriculum, so it is
replaced. All new candidates start from S40 with `reasoned_strict` and no easy-first phase
(`--easy-fraction 0`), in this order:

| # | Run | Changes |
|---|---|---|
| E1 | `grpo-strict-nocurriculum` | 150 steps, seed 2026 |
| E2 | `grpo-strict-nocurriculum-continued` | E1's adapter, 150 more steps, seed 2027 |
| E3 | `grpo-strict-nocurriculum-16gen` | 16 generations per group, 100 steps |

The development target, confirmation rule and stop rule are unchanged. The development
and confirmation sets are still unscored.
