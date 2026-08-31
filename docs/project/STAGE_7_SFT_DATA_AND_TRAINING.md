# Stage 7: Generate SFT Data and Train (Gate G7)

This governance and technical specification document details the generation, schema compliance, split isolation guarantees, Qwen2.5 chat template rendering, loss-masking contracts, and training configuration for the Supervised Fine-Tuning (SFT) phase of AtlasOps.

---

## 1. Governance & Leakage Prevention Invariants

To maintain strict scientific integrity and prevent benchmark contamination:
1. **Training Partition Quarantine**: SFT trajectory generation is strictly bounded to the **Train Split** ($|T_{\\text{train}}| = 16$).
2. **Zero Test-Set Leakage**:
   $$T_{\\text{train}} \\cap T_{\\text{val}} = \\emptyset \\quad \\text{and} \\quad T_{\\text{train}} \\cap T_{\\text{test}} = \\emptyset$$
   Neither the 6 validation scenarios nor the 6 held-out test scenarios are ever exposed to trajectory generation or SFT training.
3. **Reproducible Frozen Corpus**: The canonical dataset `data/sft_corpus_train.jsonl` is cryptographically hashed with SHA-256 and registered in `artifacts/evidence/stage7/sft_corpus_manifest.json`.

---

## 2. Dataset Architecture & Format

The dataset uses the standard `openai-tool-messages-v1` / ChatML schema:
- **Assistant Native Tool Calling**: Every tool invocation is represented as a native `tool_calls` message with structured arguments.
- **Tool Observation Pairing**: Each tool call is immediately followed by a `role: "tool"` observation matching the `tool_call_id`.
- **Structured Conclusions**: Final diagnoses, severity triage assessments, and postmortem paths are recorded as assistant turns.

### Dataset Summary Statistics

| Dimension | Metric | Detail |
| :--- | :---: | :--- |
| **Total SFT Examples** | **64** | 4 multi-agent roles $\\times$ 16 training scenarios |
| **Total Tool Turns** | **80** | Native structured tool executions |
| **Role Distribution** | **16 each** | `triage` (16), `diagnosis` (16), `remediation` (16), `comms` (16) |
| **Tier Distribution** | **4 Tiers** | `single_fault` (20), `cascade` (12), `multi_fault` (12), `named_replays` (20) |
| **Canonical LF SHA-256** | `523cad3478e2018ebb830bab973bc02811045c6131dd0bf8f59328d756287e81` | Cross-platform deterministic digest |

---

## 3. Qwen2.5 SFT Chat Template & Loss Masking Contract

1. **Canonical Schema Injection**: During tokenization, `training.sft_rendering` injects role-specific system prompts from `agents/prompts/` and runtime tool schemas from `agents.tool_policy.ROLE_ALLOWED_TOOLS`.
2. **Assistant-Only Loss Masking**: The project-owned Jinja template (`training/templates/qwen2_5_tool_sft.jinja`) wraps assistant tool calls and text in `{% generation %}` blocks.
3. **Loss Application**: Cross-entropy loss is applied strictly to assistant-generated tokens (tool calls, arguments, and conclusions). All system prompts, user context, tool definitions, and environment observations are masked out ($-\\infty$ loss weight).

---

## 4. SFT Hyperparameter Specification

Saved in `artifacts/evidence/stage7/sft_training_config.json`:

```json
{
  "base_model": "Qwen/Qwen2.5-7B-Instruct",
  "quantization": "4-bit NF4",
  "lora_r": 16,
  "lora_alpha": 32,
  "lora_dropout": 0.05,
  "target_modules": [
    "q_proj", "k_proj", "v_proj", "o_proj",
    "gate_proj", "up_proj", "down_proj"
  ],
  "learning_rate": 0.0002,
  "batch_size": 2,
  "gradient_accumulation_steps": 4,
  "max_seq_length": 2048,
  "num_train_epochs": 3,
  "optimizer": "paged_adamw_8bit",
  "assistant_only_loss": true,
  "train_corpus_sha256": "523cad3478e2018ebb830bab973bc02811045c6131dd0bf8f59328d756287e81"
}
```

---

## 5. Gate G7 Acceptance Criteria

Gate G7 is verified by automated unit tests in `tests/test_stage7_sft_pipeline.py`:
- `test_sft_corpus_and_manifest_exist`: **PASS** (Corpus and manifest generated and validated).
- `test_corpus_hash_matches_manifest`: **PASS** (Cryptographic SHA-256 integrity verified).
- `test_strict_split_isolation_no_val_or_test_leakage`: **PASS** (0 validation/test scenarios in corpus).
- `test_role_and_tier_distribution`: **PASS** (All 4 roles and 4 tiers represented).
- `test_schema_and_tool_call_message_pairing`: **PASS** (100% tool message pairing verified).
- `test_qwen_template_renderability_and_loss_masking`: **PASS** (Template rendering verified).
- `test_sft_training_config_integrity`: **PASS** (Training config matches corpus hash).

**Gate G7 Status**: **`PASS`**
