"""Memory-bounded causal-LM cross entropy for supervised SFT targets."""

from __future__ import annotations

import inspect
from functools import wraps
from types import MethodType
from typing import Any

_TOKEN_CHUNK_SIZE = 128


def chunked_causal_lm_loss(
    logits: Any,
    labels: Any,
    vocab_size: int,
    num_items_in_batch: Any = None,
    ignore_index: int = -100,
    shift_labels: Any = None,
    **_kwargs: Any,
) -> Any:
    """Match Transformers' causal-LM loss while checkpointing 128-token chunks."""
    import torch
    from torch.nn import functional
    from torch.utils.checkpoint import checkpoint

    if shift_labels is None:
        labels = functional.pad(labels, (0, 1), value=ignore_index)
        shift_labels = labels[..., 1:].contiguous()

    flat_logits = logits.view(-1, vocab_size)
    flat_labels = shift_labels.to(device=flat_logits.device).view(-1)
    supervised_indices = torch.nonzero(
        flat_labels != ignore_index, as_tuple=False
    ).flatten()

    def chunk_loss(
        selected_from: torch.Tensor,
        targets: torch.Tensor,
        indices: torch.Tensor,
    ) -> torch.Tensor:
        selected_logits = selected_from.index_select(0, indices).float()
        selected_targets = targets.index_select(0, indices)
        return functional.cross_entropy(
            selected_logits,
            selected_targets,
            ignore_index=ignore_index,
            reduction="sum",
        )

    chunk_losses = [
        checkpoint(
            chunk_loss,
            flat_logits,
            flat_labels,
            supervised_indices[start : start + _TOKEN_CHUNK_SIZE],
            use_reentrant=False,
        )
        for start in range(0, supervised_indices.numel(), _TOKEN_CHUNK_SIZE)
    ]
    if chunk_losses:
        summed_loss = torch.stack(chunk_losses).sum()
    else:
        summed_loss = flat_logits[:0].float().sum()

    if num_items_in_batch is not None:
        if isinstance(num_items_in_batch, torch.Tensor):
            num_items_in_batch = num_items_in_batch.to(device=summed_loss.device)
        return summed_loss / num_items_in_batch
    if supervised_indices.numel() == 0:
        return summed_loss + summed_loss.new_full((), float("nan"))
    return summed_loss / supervised_indices.numel()


def install_supervised_logits(model: Any) -> None:
    """Bind a per-model Qwen forward wrapper that materializes only loss logits."""
    import torch
    from torch.nn import functional

    original_forward = model.forward
    original_function = getattr(original_forward, "__func__", None)
    if original_function is None:
        raise TypeError("Selective supervised loss requires a bound model forward method")
    if getattr(original_function, "_atlasops_supervised_logits", False):
        return

    signature = inspect.signature(original_forward)
    parameters = signature.parameters
    if not {"labels", "logits_to_keep", "use_cache"}.issubset(parameters):
        raise TypeError("Model forward lacks the pinned Qwen selective-logit interface")
    variadic_name = next(
        (
            name
            for name, parameter in parameters.items()
            if parameter.kind is inspect.Parameter.VAR_KEYWORD
        ),
        None,
    )

    @wraps(original_function)
    def supervised_forward(self, *args, **kwargs):
        bound = signature.bind_partial(*args, **kwargs)
        labels = bound.arguments.get("labels")
        if labels is None:
            return original_forward(*args, **kwargs)
        if not isinstance(labels, torch.Tensor) or labels.ndim != 2 or labels.shape[0] != 1:
            raise ValueError("Selective supervised loss requires labels with batch size one")

        requested_logits = bound.arguments.get("logits_to_keep", 0)
        if isinstance(requested_logits, torch.Tensor):
            if requested_logits.numel() > 0:
                raise ValueError("Labeled SFT calls cannot provide logits_to_keep")
        elif requested_logits not in (None, 0):
            raise ValueError("Labeled SFT calls cannot provide logits_to_keep")

        padded_labels = functional.pad(labels, (0, 1), value=-100)
        shifted_labels = padded_labels[..., 1:].contiguous()
        flat_shifted_labels = shifted_labels.view(-1)
        selected_positions = torch.nonzero(
            flat_shifted_labels != -100, as_tuple=False
        ).flatten()
        selected_labels = flat_shifted_labels.index_select(0, selected_positions)

        input_ids = bound.arguments.get("input_ids")
        inputs_embeds = bound.arguments.get("inputs_embeds")
        model_inputs = input_ids if isinstance(input_ids, torch.Tensor) else inputs_embeds
        index_device = model_inputs.device if isinstance(model_inputs, torch.Tensor) else labels.device
        logits_to_keep = selected_positions.to(device=index_device)

        num_items_in_batch = None
        if "num_items_in_batch" in parameters:
            num_items_in_batch = bound.arguments.pop("num_items_in_batch", None)
        elif variadic_name is not None:
            model_kwargs = dict(bound.arguments.get(variadic_name, {}))
            num_items_in_batch = model_kwargs.pop("num_items_in_batch", None)
            bound.arguments[variadic_name] = model_kwargs

        bound.arguments["labels"] = None
        bound.arguments["logits_to_keep"] = logits_to_keep
        bound.arguments["use_cache"] = False
        output = original_forward(*bound.args, **bound.kwargs)
        if not isinstance(getattr(output, "logits", None), torch.Tensor):
            raise TypeError("Selective supervised loss requires tensor model logits")
        output["loss"] = chunked_causal_lm_loss(
            output.logits,
            labels=None,
            vocab_size=model.config.vocab_size,
            num_items_in_batch=num_items_in_batch,
            shift_labels=selected_labels,
        )
        return output

    supervised_forward._atlasops_supervised_logits = True
    model.forward = MethodType(supervised_forward, model)
