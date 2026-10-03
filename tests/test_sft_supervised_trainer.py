"""Metric alignment tests for selected supervised-token logits."""

from collections import defaultdict
from types import SimpleNamespace

import pytest

torch = pytest.importorskip("torch")

from transformers import Trainer

from training.sft_supervised_trainer import SupervisedLogitsSFTTrainer


def _trainer(monkeypatch, model, loss, outputs):
    trainer = SupervisedLogitsSFTTrainer.__new__(SupervisedLogitsSFTTrainer)
    trainer.args = SimpleNamespace(use_liger_kernel=False)
    trainer._metrics = {
        "train": defaultdict(list),
        "eval": defaultdict(list),
    }
    trainer._total_train_tokens = 0
    gathered = []

    def gather_for_metrics(value):
        gathered.append(value.detach().clone())
        return value

    trainer.accelerator = SimpleNamespace(gather_for_metrics=gather_for_metrics)
    calls = {}

    def base_compute_loss(
        self, called_model, called_inputs, return_outputs, num_items_in_batch
    ):
        calls.update({
            "model": called_model,
            "inputs": called_inputs,
            "return_outputs": return_outputs,
            "num_items_in_batch": num_items_in_batch,
        })
        called_inputs.pop("labels")
        return loss, outputs

    monkeypatch.setattr(Trainer, "compute_loss", base_compute_loss)
    return trainer, calls, gathered


@pytest.mark.parametrize("use_attention_mask", [True, False])
def test_selected_logits_preserve_loss_targets_tokens_and_accuracy(
    monkeypatch, use_attention_mask
):
    labels = torch.tensor([[-100, 4, -100, 2, 5, -100, 1]])
    original_labels = labels.clone()
    predictions = torch.tensor([[4, 2, 0, 1]])
    logits = torch.full((1, 4, 7), -2.0)
    logits.scatter_(2, predictions.unsqueeze(-1), 2.0)
    loss = torch.tensor(1.75, requires_grad=True)
    outputs = {"supervised_predictions": logits.argmax(dim=-1)}
    model = SimpleNamespace(training=True)
    trainer, calls, gathered = _trainer(monkeypatch, model, loss, outputs)
    inputs = {
        "input_ids": torch.ones((1, 7), dtype=torch.long),
        "labels": labels,
    }
    if use_attention_mask:
        inputs["attention_mask"] = torch.ones((1, 7), dtype=torch.long)
    else:
        inputs["position_ids"] = torch.arange(7).unsqueeze(0)
    num_items = torch.tensor(4)

    result_loss, result_outputs = trainer.compute_loss(
        model,
        inputs,
        return_outputs=True,
        num_items_in_batch=num_items,
    )

    assert result_loss is loss
    assert result_outputs is outputs
    assert calls["model"] is model
    assert calls["return_outputs"] is True
    assert calls["num_items_in_batch"] is num_items
    assert calls["inputs"] is not inputs
    assert torch.equal(inputs["labels"], original_labels)
    assert trainer._total_train_tokens == 7
    assert trainer._metrics["train"]["num_tokens"] == [7]
    assert trainer._metrics["train"]["mean_token_accuracy"] == [0.75]
    assert [int(value) for value in gathered] == [7, 3, 4]


def test_empty_supervision_keeps_zero_accuracy_and_eval_token_counter(
    monkeypatch,
):
    labels = torch.full((1, 5), -100, dtype=torch.long)
    loss = torch.tensor(0.0)
    outputs = {"supervised_predictions": torch.empty((1, 0), dtype=torch.long)}
    model = SimpleNamespace(training=False)
    trainer, _, gathered = _trainer(monkeypatch, model, loss, outputs)
    trainer._total_train_tokens = 23
    inputs = {
        "labels": labels,
        "position_ids": torch.arange(5).unsqueeze(0),
    }

    result = trainer.compute_loss(model, inputs)

    assert result is loss
    assert trainer._total_train_tokens == 23
    assert trainer._metrics["eval"]["num_tokens"] == [23]
    assert trainer._metrics["eval"]["mean_token_accuracy"] == [0.0]
    assert [int(value) for value in gathered] == [0, 0]
