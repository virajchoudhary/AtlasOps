"""Numerical equivalence checks for the bounded causal-LM loss."""

import inspect

import pytest

torch = pytest.importorskip("torch")
import torch.nn.functional as F

from training.sft_chunked_loss import (
    chunked_causal_lm_loss,
    install_supervised_logits,
)

IGNORE_INDEX = -100


def _reference_loss(
    logits,
    labels,
    vocab_size,
    *,
    num_items_in_batch=None,
    ignore_index=IGNORE_INDEX,
    shift_labels=None,
    **_kwargs,
):
    reference_logits = logits.float()
    if shift_labels is None:
        labels = F.pad(labels, (0, 1), value=ignore_index)
        shift_labels = labels[..., 1:].contiguous()
    reference_logits = reference_logits.view(-1, vocab_size)
    shift_labels = shift_labels.to(reference_logits.device).view(-1)
    reduction = "sum" if num_items_in_batch is not None else "mean"
    loss = F.cross_entropy(
        reference_logits,
        shift_labels,
        ignore_index=ignore_index,
        reduction=reduction,
    )
    if num_items_in_batch is not None:
        loss = loss / num_items_in_batch
    return loss


def _assert_equivalent(base_logits, labels, vocab_size, **loss_kwargs):
    reference_logits = base_logits.detach().clone().requires_grad_(True)
    chunked_logits = base_logits.detach().clone().requires_grad_(True)

    reference = _reference_loss(
        reference_logits, labels, vocab_size, **loss_kwargs
    )
    chunked = chunked_causal_lm_loss(
        chunked_logits, labels, vocab_size, **loss_kwargs
    )
    torch.testing.assert_close(
        chunked, reference, rtol=2e-5, atol=2e-6, equal_nan=True
    )
    assert chunked.dtype == torch.float32

    reference.backward()
    chunked.backward()
    torch.testing.assert_close(
        chunked_logits.grad, reference_logits.grad, rtol=3e-5, atol=3e-6
    )


@pytest.mark.parametrize(
    ("shape", "dtype", "num_items", "kwargs"),
    [
        ((2, 5, 7), torch.float16, None, {}),
        ((4, 40, 13), torch.float32, torch.tensor(103.0), {"use_cache": True}),
        ((1, 2, 9), torch.bfloat16, 3, {}),
    ],
)
def test_chunked_loss_and_all_logits_gradients_match_reference(
    shape, dtype, num_items, kwargs
):
    batch_size, sequence_length, vocab_size = shape
    logits = torch.randn(shape, dtype=dtype)
    labels = torch.randint(0, vocab_size, (batch_size, sequence_length))
    labels.view(-1)[::7] = IGNORE_INDEX

    _assert_equivalent(
        logits,
        labels,
        vocab_size,
        num_items_in_batch=num_items,
        **kwargs,
    )


def test_explicit_shift_labels_match_reference():
    shape = (2, 6, 11)
    logits = torch.randn(shape, dtype=torch.float16)
    labels = torch.randint(0, shape[-1], shape[:2])
    shift_labels = torch.randint(0, shape[-1], shape[:2])
    shift_labels[:, ::3] = IGNORE_INDEX

    _assert_equivalent(
        logits,
        labels,
        shape[-1],
        shift_labels=shift_labels,
        num_items_in_batch=torch.tensor(19.0),
    )


@pytest.mark.parametrize("num_items", [None, 5])
def test_all_ignored_targets_preserve_reference_nan_or_sum_behavior(num_items):
    logits = torch.randn((2, 5, 7), dtype=torch.float16)
    labels = torch.full((2, 5), IGNORE_INDEX, dtype=torch.long)

    _assert_equivalent(
        logits,
        labels,
        7,
        num_items_in_batch=num_items,
    )
    measured_logits = logits.detach().clone().requires_grad_(True)
    loss = chunked_causal_lm_loss(
        measured_logits,
        labels,
        7,
        num_items_in_batch=num_items,
    )
    loss.backward()
    assert torch.count_nonzero(measured_logits.grad) == 0
    if num_items is None:
        assert torch.isnan(loss)
    else:
        assert loss == 0


def test_tiny_qwen_selective_logits_preserve_loss_and_every_parameter_gradient():
    pytest.importorskip("transformers")
    from transformers.models.qwen2 import Qwen2Config, Qwen2ForCausalLM

    torch.manual_seed(17)
    config = Qwen2Config(
        vocab_size=31,
        hidden_size=16,
        intermediate_size=32,
        num_hidden_layers=1,
        num_attention_heads=2,
        num_key_value_heads=2,
        max_position_embeddings=32,
        use_cache=False,
        tie_word_embeddings=False,
    )
    reference_model = Qwen2ForCausalLM(config).eval()
    selective_model = Qwen2ForCausalLM(config).eval()
    selective_model.load_state_dict(reference_model.state_dict())
    install_supervised_logits(selective_model)
    install_supervised_logits(selective_model)

    input_ids = torch.randint(0, config.vocab_size, (1, 12))
    labels = input_ids.clone()
    labels[:, :2] = IGNORE_INDEX
    labels[:, 4:6] = IGNORE_INDEX
    labels[:, 9] = IGNORE_INDEX
    shifted_labels = F.pad(labels, (0, 1), value=IGNORE_INDEX)[..., 1:].contiguous()
    positions = torch.nonzero(
        shifted_labels.reshape(-1) != IGNORE_INDEX, as_tuple=False
    ).flatten()

    assert inspect.signature(selective_model.forward) == inspect.signature(
        reference_model.forward
    )
    reference_output = reference_model(
        input_ids=input_ids,
        labels=labels,
        use_cache=False,
    )
    selective_output = selective_model(
        input_ids=input_ids,
        labels=labels,
        use_cache=True,
    )
    assert "logits" not in selective_output
    torch.testing.assert_close(
        selective_output["supervised_predictions"],
        reference_output.logits.index_select(1, positions).argmax(dim=-1),
    )
    torch.testing.assert_close(
        selective_output["loss"], reference_output.loss, rtol=2e-5, atol=2e-6
    )

    assert "loss" in selective_output
    reference_output.loss.backward()
    selective_output["loss"].backward()
    reference_parameters = dict(reference_model.named_parameters())
    selective_parameters = dict(selective_model.named_parameters())
    assert reference_parameters.keys() == selective_parameters.keys()
    for name, reference_parameter in reference_parameters.items():
        selective_parameter = selective_parameters[name]
        assert reference_parameter.grad is not None, name
        assert selective_parameter.grad is not None, name
        torch.testing.assert_close(
            selective_parameter.grad,
            reference_parameter.grad,
            rtol=3e-5,
            atol=3e-6,
            msg=f"gradient mismatch for {name}",
        )

    for parameter in (*reference_parameters.values(), *selective_parameters.values()):
        parameter.grad = None
    num_items = torch.tensor(23.0)
    reference_counted = reference_model(
        input_ids=input_ids,
        labels=labels,
        num_items_in_batch=num_items,
        use_cache=False,
    )
    selective_counted = selective_model(
        input_ids=input_ids,
        labels=labels,
        num_items_in_batch=num_items,
        use_cache=False,
    )
    torch.testing.assert_close(
        selective_counted["loss"],
        reference_counted.loss,
        rtol=2e-5,
        atol=2e-6,
    )

    reference_plain = reference_model(input_ids=input_ids, use_cache=False)
    selective_plain = selective_model(input_ids=input_ids, use_cache=False)
    assert selective_plain.loss is None
    torch.testing.assert_close(selective_plain.logits, reference_plain.logits)

    with pytest.raises(ValueError, match="logits_to_keep"):
        selective_model(
            input_ids=input_ids,
            labels=labels,
            logits_to_keep=1,
            use_cache=False,
        )
