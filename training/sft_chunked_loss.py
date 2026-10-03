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
    from torch.utils.checkpoint import checkpoint

    original_forward = model.forward
    original_function = getattr(original_forward, "__func__", None)
    if original_function is None:
        raise TypeError("Selective supervised loss requires a bound model forward method")
    if getattr(original_function, "_atlasops_supervised_logits", False):
        return
    if (
        not isinstance(getattr(model, "model", None), torch.nn.Module)
        or not isinstance(getattr(model, "lm_head", None), torch.nn.Module)
        or getattr(getattr(model, "config", None), "model_type", None) != "qwen2"
    ):
        raise TypeError("Selective supervised loss requires the pinned Qwen core and LM head")

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

        num_items_in_batch = None
        if "num_items_in_batch" in parameters:
            num_items_in_batch = bound.arguments.pop("num_items_in_batch", None)
        elif variadic_name is not None:
            model_kwargs = dict(bound.arguments.get(variadic_name, {}))
            num_items_in_batch = model_kwargs.pop("num_items_in_batch", None)
            bound.arguments[variadic_name] = model_kwargs

        bound.arguments["use_cache"] = False
        bound.arguments.pop("labels", None)
        bound.arguments.pop("logits_to_keep", None)
        core_kwargs = dict(bound.arguments)
        if variadic_name is not None:
            extra_kwargs = core_kwargs.pop(variadic_name, {})
            core_kwargs.update(extra_kwargs)
        hidden_output = self.model(**core_kwargs)
        hidden_states = getattr(hidden_output, "last_hidden_state", None)
        if not isinstance(hidden_states, torch.Tensor):
            hidden_states = hidden_output[0]
        if hidden_states.ndim != 3 or hidden_states.shape[:2] != labels.shape:
            raise ValueError("Qwen core hidden states must retain the full labeled sequence")

        selected_positions = selected_positions.to(device=hidden_states.device)
        selected_labels = selected_labels.to(device=hidden_states.device)
        vocab_size = self.config.vocab_size

        def projected_chunk(
            full_hidden_states: torch.Tensor,
            positions: torch.Tensor,
            targets: torch.Tensor,
        ) -> tuple[torch.Tensor, torch.Tensor]:
            selected_hidden = full_hidden_states.index_select(1, positions)
            projected_logits = self.lm_head(selected_hidden)
            chunk_loss = functional.cross_entropy(
                projected_logits.float().view(-1, vocab_size),
                targets.view(-1),
                ignore_index=-100,
                reduction="sum",
            )
            chunk_predictions = projected_logits.detach().argmax(dim=-1).reshape(-1)
            return chunk_loss, chunk_predictions

        chunk_results = [
            checkpoint(
                projected_chunk,
                hidden_states,
                selected_positions[start : start + _TOKEN_CHUNK_SIZE],
                selected_labels[start : start + _TOKEN_CHUNK_SIZE],
                use_reentrant=False,
            )
            for start in range(0, selected_positions.numel(), _TOKEN_CHUNK_SIZE)
        ]
        if chunk_results:
            summed_loss = torch.stack([result[0] for result in chunk_results]).sum()
            supervised_predictions = torch.cat(
                [result[1] for result in chunk_results]
            ).unsqueeze(0)
        else:
            summed_loss = hidden_states[:, :0, :].float().sum()
            supervised_predictions = torch.empty(
                (1, 0), dtype=torch.long, device=hidden_states.device
            )

        if num_items_in_batch is not None:
            if isinstance(num_items_in_batch, torch.Tensor):
                num_items_in_batch = num_items_in_batch.to(device=summed_loss.device)
            loss = summed_loss / num_items_in_batch
        elif selected_positions.numel() == 0:
            loss = summed_loss + summed_loss.new_full((), float("nan"))
        else:
            loss = summed_loss / selected_positions.numel()
        return {
            "loss": loss,
            "supervised_predictions": supervised_predictions,
        }

    supervised_forward._atlasops_supervised_logits = True
    model.forward = MethodType(supervised_forward, model)
