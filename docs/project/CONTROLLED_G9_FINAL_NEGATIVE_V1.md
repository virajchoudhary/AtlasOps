# Controlled G9: Final Negative Result

5 October 2026. The project lead's final stop rule is reached. Controlled G9
is frozen as experimentally unsuccessful; no further GRPO training retries
are authorized. This is not infrastructure failure and not a live G4 result.

## Preserved Experiments

The genuine replacement pilot executed two optimizer steps and four policy
completions. All four actions were malformed/blocked and received objective
reward -1. Both groups had zero reward-driven advantages. Gradients were
finite, but no reward-driven GRPO learning was established and no acceptable
SFT+GRPO checkpoint was saved. Its full negative archive remains unchanged:
SHA-256 `39834cc3911bf6e32deec509a1c139d905e17aee802563abcb2ce3802f23cd33`;
manifest `c8faa1afb8948c504a23fa45f57edf76fc4eb1f47a4074623aed4659ed1809cc`.

Read-only comparison confirmed that v17's OpenAI-style messages are rendered
as Qwen ChatML with name/arguments tool-call envelopes, whereas the pilot
used bare state JSON and a different three-key action contract. None of the
four failed completions was a valid SFT-native call; causal attribution to
the interface mismatch was not established. The 17 actual remediation
examples contained no kubectl_scale target. Frozen diagnosis SHA-256:
`1216a09fc5134b8efa4932d3c060245f87139171690cea764a8c85c7a24b4d38`.

The final authorized inference-only diagnostic used the project-owned v17
template, the same initial controlled Train state and the same two allowed
tools. The predeclared parser admitted only one complete tool_call envelope
with exactly name/arguments, valid schema, unchanged policy and target checks;
no prose extraction, JSON repair or invented recovery claim.

Exactly eight sequential samples used seed 2026, temperature 1, top_p 1,
top_k 50 and at most 192 new tokens. Exact Python 3.12.11 and all 72 frozen
package versions were verified, with one free T4 exposed. Kaggle Internet
was independently observed OFF before pretrained inference; direct outbound
probes failed. Base/tokenizer revision remained
`Qwen/Qwen2.5-7B-Instruct@a09a35458c702b33eeacc393d103063234e8bc28`;
v17 weight `f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff`
and parent manifest `7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440`
were unchanged.

Result: **0/8 admissible actions**, zero optimizer steps, no gradients or
reward-based updates, unchanged Base/parent inventories and matching pre/post
hashes for all 392 LoRA tensors. The final training pilot was therefore not
launched. All raw completions/tokens and deterministic parse results are
preserved in [the frozen evidence](../../artifacts/evidence/stage9/final-aligned-diagnostic-v1/diagnostic/samples.jsonl).
The evaluated source is archived as evidence, not enabled as a new runtime.

Complete archive SHA-256:
`6137ce1d88eada54152b0c58bb7dd02f9c8758ddacf73adfafffb5a8f598e6c8`.
Diagnostic manifest:
`e31d898cc31e72c1099f092b1952c540024d0c41a84cdcb6745fd09508b99403`.
Raw samples:
`84e30346b8948cca44b164f69106fbfbba8bb189f31e892aa0ff7f6d4034439b`.
The complete local archive is outside ordinary Git; compact raw diagnostic
evidence and source hashes are tracked without model weights.
Independent curated Luna Max read-only verification passed the raw archive,
eight parse results, matching 392 tensor hashes, runtime pins and final-negative
decision; see the tracked INDEPENDENT_REVIEW.json. The archive does not include
the earlier diagnosis JSON or echo the exact lock hash/install command.
The unchanged diagnosis is preserved separately alongside the tracked evidence;
root's preserved tool call used the frozen lock with require-hashes. Neither
gap is silently rewritten into the immutable archive.

## Interpretation

Samples 1, 3 and 6 contained bare name/arguments JSON, including scale
proposals, but lacked the predeclared required envelope. They are not
retrospectively admitted. This is a negative result under the tested canonical
serialization contract, not proof that all semantic action generation is
impossible. Zero of eight does not prove the population probability is exactly
zero. No sampling, checkpoint, parser or reward tuning followed these outputs.

Final reporting uses only models and results that actually exist: pinned Base,
the reloadable v17 SFT adapter, the preserved prior diagnostic comparison,
negative live results and these controlled negatives. There is no SFT+GRPO arm.
Do not substitute tiny random model-free smoke artifacts, KL-only gradients,
mock profiles or an absent checkpoint into evaluation. No new Validation,
Test/Leaderboard, live incident, SFT retraining or final certification is
authorized by this report.

G4 remains frozen NOT_PASSED, with attempts 015/016/017 and all security/
methodological evidence unchanged. The original G8 live resolution criterion
is unmet. The stopped Kaggle session is not a deployment or recovery claim.
