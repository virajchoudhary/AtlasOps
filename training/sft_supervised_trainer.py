"""TRL SFTTrainer metrics for models returning only supervised-token logits."""

from __future__ import annotations

import torch
import torch.nn.functional as F
from transformers import Trainer
from trl import SFTTrainer


class SupervisedLogitsSFTTrainer(SFTTrainer):
    """Use the base Trainer loss path and align metrics with selected logits."""

    def training_step(self, *args, **kwargs):
        with torch.autograd.graph.save_on_cpu(pin_memory=True):
            return super().training_step(*args, **kwargs)

    def compute_loss(self, model, inputs, return_outputs=False, num_items_in_batch=None):
        mode = "train" if model.training else "eval"
        loss, outputs = Trainer.compute_loss(
            self,
            model,
            dict(inputs),
            return_outputs=True,
            num_items_in_batch=num_items_in_batch,
        )

        if mode == "train":
            if "attention_mask" in inputs:
                token_count = self.accelerator.gather_for_metrics(
                    inputs["attention_mask"].sum()
                ).sum().item()
            elif "position_ids" in inputs:
                local_count = torch.tensor(
                    inputs["position_ids"].size(1),
                    device=inputs["position_ids"].device,
                )
                token_count = self.accelerator.gather_for_metrics(local_count).sum().item()
            else:
                raise ValueError("Expected 'attention_mask' or 'position_ids' in inputs.")
            self._total_train_tokens += token_count
        self._metrics[mode]["num_tokens"] = [self._total_train_tokens]

        if "labels" in inputs and not self.args.use_liger_kernel:
            shifted_labels = F.pad(inputs["labels"], (0, 1), value=-100)[..., 1:].contiguous()
            valid_mask = shifted_labels != -100
            selected_labels = shifted_labels[valid_mask].to(outputs.logits.device)
            predictions = outputs.logits.argmax(dim=-1).reshape(-1)
            if predictions.numel() != selected_labels.numel():
                raise ValueError("Selected logits do not align with supervised shifted labels.")

            correct_tokens = (predictions == selected_labels).sum()
            total_tokens = valid_mask.sum().to(outputs.logits.device)
            correct_tokens = self.accelerator.gather_for_metrics(correct_tokens)
            total_tokens = self.accelerator.gather_for_metrics(total_tokens)
            total_sum = total_tokens.sum()
            accuracy = (
                (correct_tokens.sum() / total_sum).item() if total_sum > 0 else 0.0
            )
            self._metrics[mode]["mean_token_accuracy"].append(accuracy)

        return (loss, outputs) if return_outputs else loss
