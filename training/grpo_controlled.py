"""Prospective Train-only GRPO in a closed, explicitly synthetic environment."""

from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import signal
from collections.abc import Mapping
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.approval import ApprovalGate, approval_mode_for_severity
from agents.coordinator import _TOOL_PARAMETER_SCHEMAS, _check_tool_policy
from config.splits import get_split
from training.grpo_environment import parse_policy_action
from training.grpo_observation_first import ObservationFirstGRPOMixin, _column_values
from training.grpo_reward import score_direct_action_step

PROFILE = "controlled-g9-capacity-v1"
SCENARIO = "single_fault/sf-002"
MODEL = "Qwen/Qwen2.5-7B-Instruct"
REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
PARENT_MANIFEST_SHA256 = "7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440"
PARENT_WEIGHT_SHA256 = "f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff"
CLASSIFICATION = "CONTROLLED_SYNTHETIC_TRAINING"
HYPERPARAMETERS = {
    "seed": 2026,
    "max_steps": 2,
    "num_generations": 2,
    "per_device_train_batch_size": 2,
    "gradient_accumulation_steps": 1,
    "learning_rate": 1e-6,
    "beta": 0.04,
    "max_prompt_length": None,
    "max_completion_length": 192,
    "num_iterations": 1,
    "temperature": 1.0,
    "top_p": 1.0,
    "top_k": 50,
}


def canonical_bytes(value: Any) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_bytes(value)).hexdigest()


class EpisodeLedger:
    """Exclusive raw evidence file; persist each boundary before proceeding."""

    def __init__(self, path: Path):
        self.stream = path.open("x", encoding="utf-8")
        self.sequence = 0

    def append(self, event: str, data: Mapping[str, Any]) -> None:
        self.sequence += 1
        record = {
            "sequence": self.sequence,
            "observed_at": datetime.now(UTC).isoformat(),
            "event": event,
            "classification": CLASSIFICATION,
            "certification_status": "NOT_CERTIFIED",
            **dict(data),
        }
        self.stream.write(canonical_bytes(record).decode("utf-8") + "\n")
        self.stream.flush()
        os.fsync(self.stream.fileno())

    def close(self) -> None:
        self.stream.close()


class CapacityEnvironment:
    """A stateful capacity model, NOT a Kubernetes or Chaos Mesh emulator.

    Demand stays fixed during an episode. Scaling changes ready capacity;
    objective recovery means capacity covers demand. No host tool is invoked.
    """

    def __init__(self, ledger: EpisodeLedger, *, severity: str = "P2"):
        self.ledger = ledger
        self.severity = severity
        self.state: dict[str, Any] | None = None
        self.next_state: dict[str, Any] | None = None
        self.scenario: str | None = None
        self.group = 0
        self.sample = 0
        self.steps = 0

    def _observe(self) -> dict[str, Any]:
        if self.state is None:
            raise RuntimeError("Controlled environment has no active state")
        replicas = self.state["replicas"]
        demand = self.state["demand"]
        return {
            "incident_id": f"{PROFILE}-group-{self.group}",
            "alert": {
                "labels": {"alertname": "PodCPUSaturation", "namespace": "default"},
                "status": "firing" if replicas < demand else "resolved",
            },
            "triage": {"severity": self.severity},
            "observations": {
                "deployment": "paymentservice",
                "namespace": "default",
                "ready_replicas": replicas,
                "demand_units": demand,
                "capacity_units_per_replica": 1,
                "available_tool_schemas": {
                    name: _TOOL_PARAMETER_SCHEMAS[name]
                    for name in ("kubectl_scale", "kubectl_get")
                },
            },
        }

    def begin(self, scenario_id: str) -> dict[str, Any]:
        if scenario_id != SCENARIO or scenario_id not in get_split("train"):
            raise ValueError("Controlled profile admits only its frozen Train scenario")
        if self.state is not None:
            raise RuntimeError("Controlled group already active")
        self.scenario = scenario_id
        self.group += 1
        self.sample = 0
        self.state = (
            dict(self.next_state)
            if self.next_state and self.next_state["replicas"] < self.next_state["demand"]
            else {"replicas": 1, "demand": 3}
        )
        observed = self._observe()
        self.ledger.append("state_observed", {"group": self.group, "state": observed})
        return {"state": observed, "observed_at": datetime.now(UTC).isoformat()}

    def before_action(self, snapshot: Mapping[str, Any], index: int) -> dict[str, Any]:
        if index != self.sample:
            raise ValueError("Controlled sample order changed")
        # GRPO samples must be independent branches of the identical state.
        # This is an exact memory clone, not a claim of physical reset equivalence.
        observations = snapshot["observations"]
        self.state = {
            "replicas": observations["ready_replicas"],
            "demand": observations["demand_units"],
        }
        observed = self._observe()
        self.ledger.append(
            "branch_reset", {"group": self.group, "sample": index, "state": observed}
        )
        return {"state": observed, "observed_at": datetime.now(UTC).isoformat()}

    def verify(self) -> dict[str, Any]:
        if self.state is None:
            raise RuntimeError("Objective state unavailable")
        replicas = self.state["replicas"]
        demand = self.state["demand"]
        checks = [
            {
                "name": "capacity_covers_demand",
                "required": True,
                "passed": replicas >= demand,
                "observed": {"capacity": replicas, "demand": demand},
            },
            {
                "name": "ready_capacity_positive",
                "required": True,
                "passed": replicas > 0,
                "observed": replicas,
            },
        ]
        resolved = all(check["passed"] for check in checks)
        return {
            "verification_status": "passed" if resolved else "failed",
            "env_resolved": resolved,
            "checks": checks,
        }

    def execute(
        self, raw_completion: str, state: Mapping[str, Any], index: int,
        *, approval_gate: ApprovalGate | None = None, approval_permit: Any = None,
        approval_decision: str = "missing",
    ) -> dict[str, Any]:
        if index != self.sample or digest(state) != digest(self._observe()):
            raise ValueError("Controlled action is not bound to the observed state")
        self.ledger.append(
            "action_proposed",
            {"group": self.group, "sample": index, "state": state,
             "raw_completion": raw_completion},
        )
        action = None
        category = None
        approval_outcome = "not_applicable"
        try:
            action = parse_policy_action(raw_completion)
            if set(json.loads(raw_completion)) != {
                "tool", "arguments", "agent_claimed_resolved"
            }:
                raise ValueError("Extra policy fields are not admitted")
        except (TypeError, ValueError):
            category = "invalid_action"
        if category is None:
            tool = action["tool"]
            arguments = action["arguments"]
            # Validate schema before policy's integer conversions.
            from training.sft_candidate import _validate_schema

            try:
                _validate_schema(arguments, _TOOL_PARAMETER_SCHEMAS.get(tool, {}))
                category = (
                    "policy_block"
                    if _check_tool_policy("remediation", tool, arguments, dict(state))
                    else None
                )
            except (ValueError, TypeError):
                category = "invalid_action"
            if category is None and tool not in {"kubectl_scale", "kubectl_get"}:
                category = "tool_unavailable"
            if category is None and tool == "kubectl_scale":
                mode = approval_mode_for_severity(state["triage"]["severity"])
                if mode != "auto":
                    approval_outcome = (
                        "manual" if mode == "manual"
                        else approval_decision
                        if approval_decision in {"approved", "rejected", "timeout", "missing", "identity_missing"}
                        else "missing"
                    )
                    permitted = (
                        mode == "approve"
                        and approval_gate is not None
                        and approval_gate.consume_action_permit(
                            approval_permit, incident_id=state["incident_id"],
                            action_digest=approval_gate.action_digest(action),
                        )
                    )
                    if not permitted:
                        category = "approval_required"
                    else:
                        approval_outcome = "approved"
            if category is None and (
                arguments.get("namespace", "default") != "default"
                or (
                    tool == "kubectl_scale"
                    and arguments["deployment"] != "paymentservice"
                )
            ):
                category = "target_mismatch"
        executed = []
        if category is None:
            if action["tool"] == "kubectl_scale":
                self.state["replicas"] = action["arguments"]["replicas"]
            executed.append(
                {"tool": action["tool"], "arguments": action["arguments"],
                 "result": {"success": True, "synthetic": True}}
            )
        try:
            verification = self.verify()
            decomposition = score_direct_action_step(
                verification, agent_claimed_resolved=False
            )
        except Exception as exc:
            self.ledger.append(
                "verifier_failure",
                {"group": self.group, "sample": index, "reward": None,
                 "raw_completion": raw_completion, "policy_action": action,
                 "executed_actions": executed, "next_state": self._observe(),
                 "failure_type": type(exc).__name__},
            )
            raise
        # Claims are retained but never affect recovery or reward, even penalties.
        reward = -1.0 if category else float(decomposition["total"])
        result = {
            "scenario_id": self.scenario,
            "policy_completion": raw_completion,
            "policy_action": action,
            "executed_actions": executed,
            "status": "blocked" if category else "ok",
            "terminal_block": category,
            "approval_decision": approval_outcome,
            "verification": verification,
            "reward": reward,
            "objective_reward_decomposition": decomposition,
            "next_state": self._observe(),
        }
        self.ledger.append(
            "transition_verified",
            {"group": self.group, "sample": index, **result},
        )
        self.sample += 1
        self.steps += 1
        return result

    def finish(self, group: Mapping[str, Any], status: str) -> None:
        self.ledger.append(
            "group_finished", {"group": self.group, "status": status,
                               "next_state": self._observe() if self.state else None}
        )
        # Fixed last-sample continuation, chosen before generation, not by reward.
        self.next_state = dict(self.state) if self.state else None
        self.state = None


class ControlledGRPOMixin(ObservationFirstGRPOMixin):
    """Reuse pinned observation-first routing, not its live-candidate scorer."""

    def _observation_reward(
        self, *, prompts: Any, completions: Any, **kwargs: Any
    ) -> list[float]:
        group = self._active_group
        if group is None or self._reward_invoked:
            raise RuntimeError("Controlled reward requires one fresh generation group")
        self._reward_invoked = True
        count = group.group_size
        raw = _column_values(completions, name="completions", expected_count=count)
        supplied_prompts = _column_values(prompts, name="prompts", expected_count=count)
        if supplied_prompts != [group.snapshot_bytes.decode("utf-8")] * count:
            raise ValueError("Controlled reward prompt changed after observation")
        for name, expected in {
            "scenario_id": [group.scenario_id] * count,
            "g9_scenario_id": [group.scenario_id] * count,
            "g9_group_token": [group.token] * count,
            "g9_observation_digest": [group.digest] * count,
            "g9_prompt_sha256": [group.prompt_sha256] * count,
            "g9_sample_index": list(range(count)),
        }.items():
            if _column_values(kwargs.get(name), name=name, expected_count=count) != expected:
                raise ValueError(f"Controlled reward binding changed: {name}")
        rewards = []
        for index, completion in enumerate(raw):
            if not isinstance(completion, str):
                raise TypeError("Controlled completion must be raw text")
            snapshot = json.loads(group.snapshot_bytes)
            snapshot.pop("instruction", None)
            admitted = self._observation_lifecycle.before_action(snapshot, index)
            if canonical_bytes(admitted["state"]) != canonical_bytes(snapshot):
                raise ValueError("Controlled branch differs from its generation state")
            result = self._observation_lifecycle.execute(completion, snapshot, index)
            reward = result.get("reward")
            if (
                isinstance(reward, bool) or not isinstance(reward, int | float)
                or not math.isfinite(reward)
            ):
                raise ValueError("Controlled verifier failure: reward unavailable")
            self._active_records.append(
                {"sample_index": index, "reward": reward, "scorable": True,
                 "result_status": result["status"], "classification": CLASSIFICATION}
            )
            rewards.append(float(reward))
        return rewards


def validate_v17_parent(checkpoint: Path) -> dict[str, Any]:
    from training.grpo_provenance import validate_sft_parent
    from training.sft_provenance import has_redirecting_path_component

    if has_redirecting_path_component(checkpoint):
        raise ValueError("Controlled parent path must not contain redirects")
    parent = validate_sft_parent(
        checkpoint, model_id=MODEL, model_revision=REVISION,
        tokenizer_id=MODEL, tokenizer_revision=REVISION,
    )
    if parent["manifest_sha256"] != PARENT_MANIFEST_SHA256:
        raise ValueError("Controlled GRPO requires the exact v17 SFT manifest")
    weight = checkpoint / "adapter_model.safetensors"
    if hashlib.sha256(weight.read_bytes()).hexdigest() != PARENT_WEIGHT_SHA256:
        raise ValueError("Controlled GRPO requires the exact v17 SFT weights")
    return parent


def admit_execution(receipt_path: Path, receipt_sha256: str) -> dict[str, Any]:
    """No model load or output creation before external authority and pins pass."""
    from training.grpo_provenance import source_identity
    from training.sft_free_t4_gate import _verify_model_inventory
    from training.sft_provenance import has_redirecting_path_component

    if has_redirecting_path_component(receipt_path):
        raise ValueError("Redirected execution receipt")
    raw = receipt_path.read_bytes()
    if len(raw) > 65536 or hashlib.sha256(raw).hexdigest() != receipt_sha256:
        raise ValueError("Execution receipt must match its external SHA-256")
    receipt = json.loads(raw)
    source = source_identity()
    protocol_path = Path(__file__).resolve().parents[1] / "docs/project/CONTROLLED_G9_ADMISSION_V1.md"
    if (
        receipt.get("profile") != PROFILE
        or receipt.get("approved_by") != "Viraj Choudhary"
        or receipt.get("execution_allowed") is not True
        or receipt.get("spend_usd_max") != 0
        or receipt.get("host") not in {"Kaggle private free T4", "Colab free T4"}
        or source["source_state"] != "clean"
        or receipt.get("source_sha") != source["code_sha"]
        or receipt.get("protocol_sha256") != hashlib.sha256(protocol_path.read_bytes()).hexdigest()
        or receipt.get("hyperparameters") != HYPERPARAMETERS
        or receipt.get("parent_manifest_sha256") != PARENT_MANIFEST_SHA256
        or receipt.get("parent_weight_sha256") != PARENT_WEIGHT_SHA256
        or receipt.get("split") != "train"
        or receipt.get("scenarios") != [SCENARIO]
        or receipt.get("train_split_sha256") != digest(list(get_split("train")))
        or not isinstance(receipt.get("local_evidence_destination"), str)
        or not receipt["local_evidence_destination"]
    ):
        raise ValueError("Controlled execution receipt differs from the admitted profile")
    start = datetime.fromisoformat(receipt["not_before"])
    end = datetime.fromisoformat(receipt["not_after"])
    now = datetime.now(UTC)
    if (
        start.tzinfo is None or end.tzinfo is None
        or not start <= now < end or not 0 < (end - start).total_seconds() <= 3600
    ):
        raise ValueError("Controlled execution permit is expired or exceeds one hour")
    checkpoint = Path(receipt["parent_path"])
    output = Path(receipt["output_path"])
    repo = Path(__file__).resolve().parents[1]
    if (
        not checkpoint.is_absolute() or not output.is_absolute()
        or output.exists() or has_redirecting_path_component(output)
        or output.resolve().is_relative_to(repo)
        or output.resolve().is_relative_to(checkpoint.resolve())
        or shutil.disk_usage(output.parent).free <= 20 * 1024**3
    ):
        raise ValueError("Controlled output needs fresh external storage over 20 GiB")
    receipt["verified_parent"] = validate_v17_parent(checkpoint)
    receipt["verified_cache"] = _verify_model_inventory(receipt)
    expected = json.loads(
        (repo / "config/sft_pilot_v4.json").read_text(encoding="utf-8")
    )["environment"]["package_versions"]
    for name, version in expected.items():
        actual = importlib.metadata.version(name)
        if actual != version and not (name == "torch" and actual == version + "+cu126"):
            raise ValueError(f"Controlled runtime package mismatch: {name}")
    if platform.python_version() != "3.12.11" or platform.system() != "Linux":
        raise ValueError("Controlled runtime requires pinned Linux Python 3.12.11")
    import torch

    if (
        not torch.cuda.is_available() or torch.cuda.device_count() != 1
        or torch.cuda.get_device_name(0) != "Tesla T4"
        or torch.cuda.get_device_capability(0) != (7, 5)
        or torch.version.cuda != "12.6"
        or torch.cuda.get_device_properties(0).total_memory < 14 * 1024**3
    ):
        raise ValueError("Controlled runtime requires one verified free Tesla T4")
    for name in ("WORLD_SIZE", "LOCAL_WORLD_SIZE", "RANK", "LOCAL_RANK"):
        if int(os.environ.get(name, "1" if "SIZE" in name else "0")) != (
            1 if "SIZE" in name else 0
        ):
            raise ValueError("Controlled runtime requires one training process")
    receipt["receipt_sha256"] = receipt_sha256
    return receipt


def run_pilot(receipt_path: Path, receipt_sha256: str) -> None:
    """Actual GRPO updates; never called by ordinary unit tests or CI."""
    receipt = admit_execution(receipt_path, receipt_sha256)
    remaining = (
        datetime.fromisoformat(receipt["not_after"]) - datetime.now(UTC)
    ).total_seconds()
    if remaining <= 0:
        raise ValueError("Controlled execution permit expired during admission")

    def deadline(_signal, _frame):
        raise TimeoutError("Controlled one-hour execution ceiling reached")

    previous_handler = signal.signal(signal.SIGALRM, deadline)
    signal.setitimer(signal.ITIMER_REAL, remaining)
    try:
        _run_admitted_pilot(receipt)
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous_handler)


def _run_admitted_pilot(receipt: dict[str, Any]) -> None:
    import torch
    from datasets import Dataset
    from peft import PeftModel, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM, AutoTokenizer, BitsAndBytesConfig, TrainerCallback,
    )
    from trl import GRPOConfig, GRPOTrainer
    from training.sft_free_t4_gate import _verify_model_inventory
    from training.sft_provenance import checkpoint_inventory, write_manifest_atomic

    output = Path(receipt["output_path"])
    parent = Path(receipt["parent_path"])
    output.mkdir()
    manifest_path = output / "controlled_grpo_manifest.json"
    manifest = {
        "profile": PROFILE, "classification": CLASSIFICATION,
        "certification_status": "NOT_CERTIFIED", "status": "started",
        "receipt": receipt, "started_at": datetime.now(UTC).isoformat(),
        "checkpoint": None, "reload_verified": False,
        "kl_reference": "adapter_disabled_pinned_base",
    }
    write_manifest_atomic(manifest_path, manifest)
    ledger = EpisodeLedger(output / "episodes.jsonl")
    try:
        os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
        torch.manual_seed(HYPERPARAMETERS["seed"])
        common = {
            "revision": REVISION, "cache_dir": receipt["verified_cache"],
            "local_files_only": True, "trust_remote_code": False,
        }
        tokenizer = AutoTokenizer.from_pretrained(MODEL, **common)
        base = AutoModelForCausalLM.from_pretrained(
            MODEL, **common, device_map={"": 0}, torch_dtype=torch.float16,
            quantization_config=BitsAndBytesConfig(
                load_in_4bit=True, bnb_4bit_quant_type="nf4",
                bnb_4bit_compute_dtype=torch.float16, bnb_4bit_use_double_quant=True,
            ),
        )
        base = prepare_model_for_kbit_training(base, use_gradient_checkpointing=True)
        validate_v17_parent(parent)
        model = PeftModel.from_pretrained(base, str(parent), is_trainable=True)
        model.config.use_cache = False
        parameters = {n: p for n, p in model.named_parameters() if p.requires_grad}
        if not parameters or any("lora_" not in n for n in parameters):
            raise ValueError("Only the v17 LoRA parameters may train")
        before = {n: p.detach().cpu().clone() for n, p in parameters.items()}
        before_hashes = {
            n: hashlib.sha256(p.view(torch.uint8).numpy().tobytes()).hexdigest()
            for n, p in before.items()
        }
        ledger.append("initial_adapter_tensors", {"tensor_sha256": before_hashes})
        gradients = []
        advantages = []

        class GradientEvidence(TrainerCallback):
            def on_pre_optimizer_step(self, args, state, control, **kwargs):
                values = [p.grad for p in parameters.values() if p.grad is not None]
                finite = bool(values) and all(torch.isfinite(v).all().item() for v in values)
                norm = sum(float(v.float().norm().item()) for v in values)
                gradients.append({"step": state.global_step, "finite": finite, "norm": norm})
                ledger.append("gradient_observed", gradients[-1])
                if not finite:
                    raise ValueError("Missing/nonfinite GRPO gradients")

        class RawGeneration(ControlledGRPOMixin, GRPOTrainer):
            def _generate_and_score_completions(self, inputs):
                result = super()._generate_and_score_completions(inputs)
                observed_advantages = result["advantages"].detach().cpu()
                if not torch.isfinite(observed_advantages).all().item():
                    raise ValueError("GRPO advantages must be finite")
                advantages.append(bool(torch.any(observed_advantages != 0).item()))
                ledger.append(
                    "generation_tokens",
                    {key: result[key].detach().cpu().tolist()
                     for key in ("prompt_ids", "completion_ids", "advantages")},
                )
                return result

        config = GRPOConfig(
            output_dir=str(output / "trainer"), **HYPERPARAMETERS,
            fp16=True, bf16=False, use_vllm=False, report_to=[],
            save_strategy="no", logging_steps=1, remove_unused_columns=False,
            dataloader_num_workers=0, optim="paged_adamw_8bit",
        )
        environment = CapacityEnvironment(ledger)
        trainer = RawGeneration(
            model=model, args=config,
            train_dataset=Dataset.from_list([{"prompt": "", "scenario_id": SCENARIO}]),
            processing_class=tokenizer, observation_lifecycle=environment,
            callbacks=[GradientEvidence()],
        )
        model.train()
        trainer.train(resume_from_checkpoint=None)
        changed = sum(
            not torch.equal(before[n], p.detach().cpu()) for n, p in parameters.items()
        )
        if (
            trainer.state.global_step != HYPERPARAMETERS["max_steps"]
            or len(gradients) != HYPERPARAMETERS["max_steps"]
            or not any(v["norm"] > 0 for v in gradients) or not changed
            or not any(advantages)
            or not any(
                len({r["reward"] for r in group["records"]}) > 1
                for group in trainer.observation_evidence
            )
            or not all(torch.isfinite(p).all().item() for p in parameters.values())
        ):
            raise ValueError("GRPO did not establish finite changed adapter parameters")
        validate_v17_parent(parent)
        _verify_model_inventory(receipt)
        model.save_pretrained(output / "adapter", safe_serialization=True)
        tokenizer.save_pretrained(output / "adapter")
        trainer.save_state()
        trainer._save_optimizer_and_scheduler(str(output / "trainer"))
        ledger.append(
            "training_finished",
            {"global_step": trainer.state.global_step, "changed_tensors": changed,
             "gradients": gradients, "trainer_log_history": trainer.state.log_history,
             "final_tensor_sha256": {
                 n: hashlib.sha256(p.detach().cpu().view(torch.uint8).numpy().tobytes()).hexdigest()
                 for n, p in parameters.items()
             }},
        )
        manifest.update(
            status="TRAINED_RELOAD_PENDING", changed_tensors=changed,
            checkpoint=checkpoint_inventory(output / "adapter", manifest_path),
        )
    except BaseException as exc:
        manifest.update(
            status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
            failure_type=type(exc).__name__,
        )
        ledger.append("training_failed", {"failure_type": type(exc).__name__, "reward": None})
        raise
    finally:
        ledger.close()
        manifest["finished_at"] = datetime.now(UTC).isoformat()
        write_manifest_atomic(manifest_path, manifest)


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--receipt", type=Path, required=True)
    parser.add_argument("--receipt-sha256", required=True)
    parser.add_argument("--preflight-only", action="store_true")
    args = parser.parse_args()
    if args.preflight_only:
        admit_execution(args.receipt, args.receipt_sha256)
        print("CONTROLLED_G9_ADMITTED_RUNTIME_ONLY; no training or output created")
    else:
        run_pilot(args.receipt, args.receipt_sha256)


if __name__ == "__main__":
    main()
