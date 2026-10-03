"""Use the pinned SDPA engine with explicitly repeated GQA key/value heads."""

from types import SimpleNamespace


def t4_sdpa_attention(module, query, key, value, attention_mask, **kwargs):
    from transformers.integrations.sdpa_attention import repeat_kv, sdpa_attention_forward

    groups = module.num_key_value_groups
    key = repeat_kv(key, groups)
    value = repeat_kv(value, groups)
    return sdpa_attention_forward(
        SimpleNamespace(is_causal=getattr(module, "is_causal", True)),
        query, key, value, attention_mask, **kwargs,
    )


def install_t4_attention(model):
    from transformers.modeling_utils import ALL_ATTENTION_FUNCTIONS
    from transformers.masking_utils import ALL_MASK_ATTENTION_FUNCTIONS

    name = "atlasops_t4_sdpa"
    ALL_ATTENTION_FUNCTIONS.register(name, t4_sdpa_attention)
    ALL_MASK_ATTENTION_FUNCTIONS.register(name, ALL_MASK_ATTENTION_FUNCTIONS["sdpa"])
    model.config._attn_implementation = name
