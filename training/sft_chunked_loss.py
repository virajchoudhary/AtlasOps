"""Memory-bounded causal-LM cross entropy for supervised SFT targets."""

from __future__ import annotations

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
    import torch.nn.functional as functional
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
