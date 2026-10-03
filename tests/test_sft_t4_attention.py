"""Explicit GQA repetition preserves the pinned Qwen attention computation."""

import copy

import pytest

torch = pytest.importorskip("torch")

from transformers import Qwen2Config, Qwen2ForCausalLM

from training.sft_t4_attention import install_t4_attention


def test_explicit_gqa_repeat_matches_dense_qwen_gradients():
    torch.manual_seed(2026)
    original = Qwen2ForCausalLM(Qwen2Config(
        vocab_size=31, hidden_size=16, intermediate_size=32,
        num_hidden_layers=1, num_attention_heads=4, num_key_value_heads=2,
    ))
    repeated = copy.deepcopy(original)
    install_t4_attention(repeated)
    inputs = torch.randint(0, 31, (1, 100))
    first = original(input_ids=inputs, labels=inputs, use_cache=False)
    second = repeated(input_ids=inputs, labels=inputs, use_cache=False)
    torch.testing.assert_close(first.logits, second.logits, rtol=3e-5, atol=3e-6)
    torch.testing.assert_close(first.loss, second.loss)
    first.loss.backward()
    second.loss.backward()
    for first_parameter, second_parameter in zip(
        original.parameters(), repeated.parameters(), strict=True,
    ):
        torch.testing.assert_close(
            first_parameter.grad, second_parameter.grad, rtol=5e-5, atol=5e-6,
        )
