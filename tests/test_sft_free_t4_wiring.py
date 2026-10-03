"""The free pilot is opt-in and leaves frozen-v4 behavior unchanged."""

from types import SimpleNamespace

from training.sft import _hyperparameters


def test_free_t4_precision_is_explicit():
    args = SimpleNamespace(
        epochs=1, lr=0.0002, batch_size=1, grad_accum=8,
        max_seq_len=8192, seed=2026, pilot_profile="free-t4-v1",
    )
    values = _hyperparameters(args)
    assert values["quantization"]["bnb_4bit_compute_dtype"] == "float16"
    assert values["bf16"] is False and values["fp16"] is True
    assert values["batch_size"] * values["gradient_accumulation_steps"] == 8
    assert values["max_sequence_length"] == 8192
    assert values["assistant_only_loss"] is True


def test_existing_argument_objects_keep_frozen_precision():
    args = SimpleNamespace(
        epochs=1, lr=0.0002, batch_size=2, grad_accum=4,
        max_seq_len=8192, seed=2026,
    )
    values = _hyperparameters(args)
    assert values["quantization"]["bnb_4bit_compute_dtype"] == "bfloat16"
    assert values["bf16"] is True
    assert "fp16" not in values
