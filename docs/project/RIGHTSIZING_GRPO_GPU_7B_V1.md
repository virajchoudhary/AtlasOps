# Right-Sizing GRPO on a GPU, Qwen2.5-7B (v1): pre-registration

26 October 2026. Classification: `EXPLORATORY_GPU_RL_EXPERIMENT`.

**This is not G9 evidence and changes no gate.** G9 remains NOT_PASSED/FROZEN. The project
lead has not reopened it, [the final negative result](CONTROLLED_G9_FINAL_NEGATIVE_V1.md)
still stands, and this run deliberately does **not** use the governed trainer
`training/grpo_controlled.py`, which admits a run only against an execution receipt the
project lead signs. It uses the experiment trainer
[`scripts/train_rightsizing_grpo.py`](../../scripts/train_rightsizing_grpo.py) on the
synthetic right-sizing environment. Nobody has reviewed it independently.

Everything below the "Pre-registration" heading was written and committed **before** any
training ran. Results, amendments and stopped runs are appended afterwards, dated, and each
amendment is committed before the run it governs.

## Why this run

[The CPU experiment](RIGHTSIZING_GRPO_CPU_V1.md) showed GRPO working end to end on
Qwen2.5-0.5B: a 40-demonstration SFT policy went from 50/128 to 128/128 on a single-use
confirmation set. It also showed the result was fragile — training collapsed after about
50 steps, and SFT on 64 demonstrations alone reached 127/128, so RL matched SFT rather than
beating it. Four things made it work at all, and all four are carried over here:

1. an answer format the start policy can already produce, so there is a gradient at all;
2. the reasoning line kept inside the scored format, so the policy cannot drop it;
3. balanced prompts in every optimizer step, because single-prompt steps collapsed the
   policy into "always round down" or "always add one";
4. a checkpoint every 10 steps, selected on a development set and confirmed once on states
   nothing has touched.

What is new here is scale: the pinned 7B model that G9 itself uses, on a real T4, in 4-bit.
The question is whether the same recipe survives the jump from 0.5B to 7B.

## Pre-registration

### Model

- `Qwen/Qwen2.5-7B-Instruct` at revision `a09a35458c702b33eeacc393d103063234e8bc28` — the
  G9 pin — downloaded on the GPU machine with `huggingface_hub.snapshot_download`, with a
  file-hash manifest kept as evidence.
- Loaded in 4-bit nf4 with float16 compute (`BitsAndBytesConfig`), then
  `prepare_model_for_kbit_training`. A free T4 holds 16 GiB and has no bfloat16.
- **Arms.** The base-model arm (base → SFT → GRPO) is the one that runs. A second arm
  starting from the **v17 SFT adapter** (SHA-256
  `f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff`) runs only if the
  project lead supplies that adapter; it is held in his private store and is not in Git.

### Task

The right-sizing environment, [`training/rightsizing_env.py`](../../training/rightsizing_env.py):

- `--action-format reasoned_strict` — one arithmetic line in the demonstrated form, then the
  action as one JSON object; only those two lines score;
- the repository reward `score_direct_action_step`, the same function controlled G9 uses;
- an invalid, malformed or policy-blocked action scores −1.

### Evaluation sets (v1.3)

The v1.2 confirmation set has been used once, so it is spent. New sets are drawn by
[`scripts/make_rightsizing_eval_sets.py --version v1.3`](../../scripts/make_rightsizing_eval_sets.py)
and committed before training as
[`artifacts/evidence/rl-gpu-7b-v1/eval-sets-v1.3.json`](../../artifacts/evidence/rl-gpu-7b-v1/eval-sets-v1.3.json).

- **Pool.** Train states that nothing has touched: no GRPO prompt in any recorded run, no
  SFT demonstration, no Train evaluation sample, not the smoke run's four prompts, not
  either v1.2 set, and not the original 64 held-out states. Each run's demonstrations are
  re-drawn from its own recorded configuration and checked against the
  `demonstrations_sha256` it wrote down, so the pool cannot shrink unnoticed. 667 of the 926
  Train states are touched; 259 remain, 74 with exact division and 185 without.
- **Development set, 64 states:** 32 exact-division and 32 inexact-division, because the
  development bar below is read per category.
- **Confirmation set, 128 states:** the 42 exact-division states the development set leaves,
  plus 86 inexact. A half-and-half confirmation set is impossible from a pool of 74 exact
  states.
- Digests: dev `116c40b3919252626f6750e48b94e4ca5e96e9b7fb508dd88f53c122c234661a`,
  confirmation `5ef5384fa858f30cf53ec95b77fd2d1037571c0acdd7531d92a4926545adaa0b`.
- Both sets are passed to every `sft` and `train` command through `--exclude-states`, so
  neither can become a training prompt or a demonstration.

### Recipe

1. **SFT warm start**, `reasoned_strict`, on Train-only demonstrations. The size is chosen by
   a fixed rule read on the **Train evaluation sample only**: at least 75% valid answers and
   between 20% and 85% solved. Try 16 demonstrations, then 32, then 48, and take the first
   that satisfies the rule. A start that already solves nearly everything leaves RL nothing
   to do; one that solves nothing gives no gradient.
2. **GRPO** from that start: `--balanced --prompts-per-step 2 --save-every 10`, 100 steps,
   learning rate 5e-5, 8 generations, temperature 1.0, beta 0,
   `--max-completion-length 96`.
3. **Selection.** Score every saved checkpoint on the development set; take the best, and on
   a tie the earliest.
4. **Confirmation.** Score the selected checkpoint **once** on the confirmation set, and its
   SFT start once on the same states.

### Success criterion, fixed in advance

The run succeeds only if **both** hold:

- at least **116 of 128** solved on the confirmation set; and
- an **exact McNemar p below 0.01** against its own SFT start on the same 128 states.

**Development bar before confirming at all:** at least 58 of 64 solved, with at least 85% of
the exact-division states and at least 85% of the inexact-division states (that is, at least
28 of 32 in each). The confirmation set is never looked at to make a decision.

### Collapse watch and the allowed changes

Training samples are read every 10 steps. If the exact-division or the inexact-division
samples fall towards 0 solved, the evidence is saved and that run is stopped. A dated
amendment then goes into this document — what was seen, what changes next — and is committed
**before** the next run. The allowed changes, in this order and no other:

1. lower the learning rate to 2e-5;
2. checkpoint more often, `--save-every 5`;
3. 4 prompts per step, still balanced;
4. a different SFT start.

If the whole list is exhausted without progress, the run stops and the result is reported as
a negative one, with every stopped run included.

### What this run cannot show

- It is not the G9 protocol, the G9 trainer, or a G9 environment. The task is synthetic:
  no Kubernetes, no Chaos Mesh, no live service.
- A result here, good or bad, does not move G9, G13, or any other gate.
- One model, one seed, one task. Nothing here generalises to incident response.

## Amendment 1 — 27 October 2026, before any GRPO run

**What changed.** The collapse watch runs *after* each GRPO run rather than interrupting it.

**Why.** The pre-registration says to read the training samples every 10 steps and stop a run
that collapses. The run happens in a Kaggle batch kernel, which does not stream its log: the
output arrives only when the kernel finishes, so there is nothing to watch while it runs and
no way to interrupt it from outside. Kaggle is the GPU available here.

**What this costs, and what it does not.** It costs GPU time on a run that has already gone
wrong — nothing else. It does not touch the evidence or the decision: `rollouts.jsonl` records
every completion with its step, so the per-10-step picture is read in full afterwards, and
`--save-every 10` means the checkpoint from before a collapse still exists and is still the one
the development set selects. On CPU this was exactly what happened — training collapsed after
about 50 steps and the step-30 checkpoint was the one selected.

**What stays.** Everything else: the allowed changes, in order, with a dated amendment
committed before each next run; the development bar; and the single use of the confirmation
set.

## Amendment 2 — 27 October 2026, before any GRPO run

**What changed.** The SFT micro-batch is 2 rather than the CPU run's 4.

**Why.** A 7B model in 4-bit with LoRA on a 15 GiB T4 has far less headroom than a 0.5B model
on a CPU with 30 GiB of RAM. The accumulated update is unchanged — micro-batches only bound
peak memory, and the optimizer still steps once per batch of 8 — so this changes how the
step is computed, not what it computes.

## Runs

*(Appended as they happen, including stopped ones.)*
