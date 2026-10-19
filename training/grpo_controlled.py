"""Prospective Train-only GRPO in a closed, explicitly synthetic environment."""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import json
import math
import os
import platform
import shutil
import signal
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from agents.approval import ApprovalGate, approval_mode_for_severity
from agents.coordinator import _TOOL_PARAMETER_SCHEMAS, _check_tool_policy
from config.splits import get_split
from training.grpo_environment import parse_policy_action
from training.grpo_observation_first import (
    ObservationFirstGRPOMixin,
    _column_values,
    _public_snapshot,
)
from training.grpo_reward import (
    ENVIRONMENTAL_GRADIENT_COMPONENT,
    ENVIRONMENTAL_REWARD_COMPONENTS,
    has_environmental_reward_signal,
    has_reward_driven_advantages,
    normalize_group_advantages,
    score_direct_action_step,
)

PROFILE = "controlled-g9-capacity-v1"
SCENARIO = "single_fault/sf-002"
MODEL = "Qwen/Qwen2.5-7B-Instruct"
REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
PARENT_MANIFEST_SHA256 = "7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440"
PARENT_WEIGHT_SHA256 = "f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff"
CLASSIFICATION = "CONTROLLED_SYNTHETIC_TRAINING"
REWARD_GRADIENT_DIAGNOSTIC_AVAILABLE = False
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


def _observation_lineage(state: Mapping[str, Any]) -> dict[str, str]:
    snapshot_bytes, observation_digest = _public_snapshot(state)
    return {
        "observation_digest": observation_digest,
        "state_prompt_sha256": hashlib.sha256(snapshot_bytes).hexdigest(),
    }


def _environmental_advantage_values(inputs: Mapping[str, Any]) -> list[float]:
    import torch

    advantages = inputs.get("advantages")
    if torch.is_tensor(advantages):
        values = advantages.detach().cpu().reshape(-1).tolist()
    elif (
        not isinstance(advantages, (str, bytes))
        and isinstance(advantages, Sequence)
    ):
        values = list(advantages)
    else:
        raise RuntimeError("Controlled GRPO loss has no environmental advantages")
    if any(
        isinstance(value, bool)
        or not isinstance(value, int | float)
        or not math.isfinite(float(value))
        for value in values
    ):
        raise RuntimeError("Controlled GRPO loss has invalid environmental advantages")
    return [float(value) for value in values]


def _optimization_input_sha256(inputs: Mapping[str, Any]) -> str:
    import torch

    if not isinstance(inputs, Mapping) or not {
        "prompt_ids",
        "completion_ids",
        "advantages",
    }.issubset(inputs):
        raise RuntimeError(
            "Controlled GRPO loss inputs do not bind prompt, completion, and advantages"
        )

    def evidence_value(value: Any) -> Any:
        if torch.is_tensor(value):
            tensor = value.detach().cpu().contiguous()
            raw = (
                tensor.reshape(-1).view(torch.uint8).numpy().tobytes()
                if tensor.numel()
                else b""
            )
            return {
                "dtype": str(tensor.dtype),
                "shape": list(tensor.shape),
                "sha256": hashlib.sha256(raw).hexdigest(),
            }
        if isinstance(value, Mapping):
            if any(not isinstance(key, str) for key in value):
                raise TypeError("GRPO loss input keys must be strings")
            return {
                key: evidence_value(value[key])
                for key in sorted(value)
            }
        if isinstance(value, (list, tuple)):
            return [evidence_value(item) for item in value]
        if value is None or isinstance(value, str | int | float | bool):
            return value
        raise TypeError("GRPO loss input contains unsupported evidence")

    try:
        payload = evidence_value(inputs)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Controlled GRPO loss input evidence is incomplete") from exc
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def _environmental_advantage_sha256(values: Sequence[float]) -> str:
    return hashlib.sha256(
        canonical_bytes([float(value) for value in values])
    ).hexdigest()


def _plain_input_values(value: Any) -> Any:
    import torch

    if torch.is_tensor(value):
        return value.detach().cpu().tolist()
    if isinstance(value, Mapping):
        return {key: _plain_input_values(value[key]) for key in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_plain_input_values(item) for item in value]
    if value is None or isinstance(value, str | int | float | bool):
        return value
    raise TypeError("GRPO signal input contains unsupported values")


def _environmental_signal_sha256(inputs: Mapping[str, Any]) -> str:
    if not isinstance(inputs, Mapping) or not {
        "prompt_ids",
        "completion_ids",
        "advantages",
    }.issubset(inputs):
        raise RuntimeError(
            "Controlled GRPO signal lacks generated tokens or environmental advantages"
        )
    payload = {
        "prompt_ids": _plain_input_values(inputs["prompt_ids"]),
        "completion_ids": _plain_input_values(inputs["completion_ids"]),
        "advantages": _environmental_advantage_values(inputs),
    }
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


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
        self.ledger.append(
            "state_observed",
            {"group": self.group, "state": observed, **_observation_lineage(observed)},
        )
        return {"state": observed, "observed_at": datetime.now(UTC).isoformat()}

    def before_action(
        self,
        snapshot: Mapping[str, Any],
        index: int,
        *,
        g9_group_token: str | None = None,
    ) -> dict[str, Any]:
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
        lineage = _observation_lineage(observed)
        self.ledger.append(
            "branch_reset",
            {
                "group": self.group,
                "sample": index,
                "state": observed,
                "g9_group_token": g9_group_token,
                **lineage,
            },
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
        approval_decision: str = "missing", g9_group_token: str | None = None,
    ) -> dict[str, Any]:
        if index != self.sample or digest(state) != digest(self._observe()):
            raise ValueError("Controlled action is not bound to the observed state")
        if g9_group_token is not None and (
            not isinstance(g9_group_token, str) or not g9_group_token.strip()
        ):
            raise ValueError("Controlled action has an invalid GRPO group token")
        lineage = _observation_lineage(state)
        self.ledger.append(
            "action_proposed",
            {"group": self.group, "sample": index, "state": state,
             "raw_completion": raw_completion, "g9_group_token": g9_group_token,
             **lineage},
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
        environmental_components = {
            name: decomposition[name]
            for name in (
                "r_verified_resolution",
                "r_required_check_coverage",
            )
        }
        environmental_reward = round(sum(environmental_components.values()), 6)
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
            "environmental_components": environmental_components,
            "environmental_reward": environmental_reward,
            "environmental_admissible": category is None and bool(executed),
            "scorable": True,
            "g9_group_token": g9_group_token,
            **lineage,
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

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        self._environmental_gradient_evidence: list[dict[str, Any]] = []
        self._environmental_group_evidence: list[dict[str, Any]] = []
        super().__init__(*args, **kwargs)

    @property
    def environmental_gradient_evidence(self) -> tuple[dict[str, Any], ...]:
        return tuple(copy.deepcopy(self._environmental_gradient_evidence))

    def compute_loss(
        self,
        model: Any,
        inputs: Any,
        return_outputs: bool = False,
        num_items_in_batch: Any = None,
    ) -> Any:
        if getattr(model, "training", None) is True:
            if not isinstance(inputs, Mapping):
                raise RuntimeError("Controlled GRPO loss inputs must be a mapping")
            optimization_input_sha256 = _optimization_input_sha256(inputs)
            environmental_advantages = _environmental_advantage_values(inputs)
            environmental_advantages_sha256 = _environmental_advantage_sha256(
                environmental_advantages
            )
            environmental_signal_sha256 = _environmental_signal_sha256(inputs)
            group_index = self.state.global_step + 1
            if self._environmental_group_evidence:
                if not 1 <= group_index <= len(self._environmental_group_evidence):
                    raise RuntimeError(
                        "Controlled GRPO loss has no matched environmental group"
                    )
                group_evidence = self._environmental_group_evidence[group_index - 1]
                if (
                    group_evidence["group"] != group_index
                    or group_evidence["optimization_input_sha256"]
                    != optimization_input_sha256
                    or group_evidence["environmental_signal_sha256"]
                    != environmental_signal_sha256
                    or group_evidence["environmental_advantages_sha256"]
                    != environmental_advantages_sha256
                ):
                    raise RuntimeError(
                        "Controlled GRPO loss inputs differ from the environmental group"
                    )
            self._measure_environmental_policy_gradient(
                model,
                inputs,
                optimization_input_sha256=optimization_input_sha256,
                environmental_signal_sha256=environmental_signal_sha256,
                environmental_advantages_sha256=environmental_advantages_sha256,
            )
        loss = super().compute_loss(
            model,
            inputs,
            return_outputs=return_outputs,
            num_items_in_batch=num_items_in_batch,
        )
        if (
            getattr(model, "training", None) is True
            and _optimization_input_sha256(inputs) != optimization_input_sha256
        ):
            raise RuntimeError("Controlled GRPO loss inputs changed during diagnostics")
        return loss

    def _measure_environmental_policy_gradient(
        self,
        model: Any,
        inputs: Mapping[str, Any],
        *,
        optimization_input_sha256: str,
        environmental_signal_sha256: str,
        environmental_advantages_sha256: str,
    ) -> None:
        import torch

        step = self.state.global_step
        if type(step) is not int or step != len(self._environmental_gradient_evidence):
            raise RuntimeError(
                "Controlled environmental gradient is not aligned to one optimizer step"
            )
        if self.use_liger_loss:
            raise RuntimeError(
                "Controlled environmental diagnostics require pinned TRL's standard loss"
            )

        parameters = tuple(parameter for parameter in model.parameters() if parameter.requires_grad)
        if not parameters:
            raise RuntimeError(
                "Controlled environmental gradient has no trainable parameters"
            )

        diagnostic = copy.copy(self)
        diagnostic.beta = 0.0
        diagnostic._metrics = copy.deepcopy(self._metrics)
        checkpointing_model = self.accelerator.unwrap_model(model)
        checkpointing_enabled = bool(
            getattr(checkpointing_model, "is_gradient_checkpointing", False)
        )
        enable_gradient_checkpointing = getattr(
            checkpointing_model, "gradient_checkpointing_enable", None
        )
        if checkpointing_enabled and not callable(enable_gradient_checkpointing):
            raise RuntimeError(
                "Cannot measure reward-policy gradient with reentrant checkpointing"
            )

        checkpointing_state = []
        if checkpointing_enabled:
            checkpointing_state = [
                (
                    module,
                    hasattr(module, "gradient_checkpointing"),
                    getattr(module, "gradient_checkpointing", None),
                    hasattr(module, "_gradient_checkpointing_func"),
                    getattr(module, "_gradient_checkpointing_func", None),
                )
                for module in checkpointing_model.modules()
            ]

        cuda_devices = {
            tensor.device.index
            if tensor.device.index is not None
            else torch.cuda.current_device()
            for tensor in (*model.parameters(), *model.buffers())
            if tensor.device.type == "cuda"
        }
        with torch.random.fork_rng(devices=sorted(cuda_devices)):
            try:
                if checkpointing_enabled:
                    # Reentrant checkpointing rejects autograd.grad.
                    enable_gradient_checkpointing(
                        gradient_checkpointing_kwargs={"use_reentrant": False}
                    )
                environmental_loss = super(
                    ControlledGRPOMixin, diagnostic
                ).compute_loss(model, inputs)
                if (
                    not torch.is_tensor(environmental_loss)
                    or environmental_loss.numel() != 1
                    or not environmental_loss.requires_grad
                ):
                    raise RuntimeError(
                        "Pinned TRL environmental-only loss is not a differentiable scalar"
                    )
                gradients = torch.autograd.grad(
                    environmental_loss, parameters, allow_unused=True
                )
            finally:
                if checkpointing_enabled:
                    for (
                        module,
                        had_checkpointing_flag,
                        checkpointing_flag,
                        had_checkpointing_func,
                        checkpointing_func,
                    ) in checkpointing_state:
                        if had_checkpointing_flag:
                            module.gradient_checkpointing = checkpointing_flag
                        elif hasattr(module, "gradient_checkpointing"):
                            delattr(module, "gradient_checkpointing")

                        if had_checkpointing_func:
                            module._gradient_checkpointing_func = checkpointing_func
                        elif hasattr(module, "_gradient_checkpointing_func"):
                            delattr(module, "_gradient_checkpointing_func")

        present_gradients = [gradient for gradient in gradients if gradient is not None]
        finite = bool(present_gradients) and all(
            torch.isfinite(gradient).all().item()
            for gradient in present_gradients
        )
        norm = (
            sum(float(gradient.float().norm().item()) for gradient in present_gradients)
            if finite
            else None
        )
        self._environmental_gradient_evidence.append(
            {
                "step": step,
                "group": step + 1,
                "finite": finite,
                "norm": norm,
                "component": ENVIRONMENTAL_GRADIENT_COMPONENT,
                "environmental_advantages_sha256": environmental_advantages_sha256,
                "environmental_signal_sha256": environmental_signal_sha256,
                "optimization_input_sha256": optimization_input_sha256,
            }
        )

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
        environmental_rewards = []
        for index, completion in enumerate(raw):
            if not isinstance(completion, str):
                raise TypeError("Controlled completion must be raw text")
            snapshot = json.loads(group.snapshot_bytes)
            snapshot.pop("instruction", None)
            admitted = self._observation_lifecycle.before_action(
                snapshot, index, g9_group_token=group.token
            )
            if canonical_bytes(admitted["state"]) != canonical_bytes(snapshot):
                raise ValueError("Controlled branch differs from its generation state")
            result = self._observation_lifecycle.execute(
                completion,
                snapshot,
                index,
                g9_group_token=group.token,
            )
            reward = result.get("reward")
            if (
                isinstance(reward, bool) or not isinstance(reward, int | float)
                or not math.isfinite(reward)
            ):
                raise ValueError("Controlled verifier failure: reward unavailable")
            environmental_components = result.get("environmental_components")
            environmental_reward = result.get("environmental_reward")
            environmental_admissible = result.get("environmental_admissible")
            lineage = {
                "g9_group_token": group.token,
                "observation_digest": group.digest,
                "state_prompt_sha256": group.prompt_sha256,
            }
            if (
                not isinstance(environmental_components, Mapping)
                or set(environmental_components)
                != set(ENVIRONMENTAL_REWARD_COMPONENTS)
                or isinstance(environmental_reward, bool)
                or not isinstance(environmental_reward, int | float)
                or not math.isfinite(environmental_reward)
                or type(environmental_admissible) is not bool
                or any(result.get(key) != value for key, value in lineage.items())
            ):
                raise ValueError(
                    "Controlled verifier lacks environmental signal or lineage evidence"
                )
            components = {
                name: environmental_components[name]
                for name in ENVIRONMENTAL_REWARD_COMPONENTS
            }
            self._active_records.append(
                {
                    "sample_index": index,
                    "reward": reward,
                    "environmental_reward": environmental_reward,
                    "environmental_components": components,
                    "environmental_admissible": environmental_admissible,
                    "scorable": result.get("scorable") is True,
                    "result_status": result["status"],
                    "terminal_block": result["terminal_block"],
                    "completion_sha256": hashlib.sha256(
                        completion.encode("utf-8")
                    ).hexdigest(),
                    **lineage,
                    "classification": CLASSIFICATION,
                }
            )
            environmental_rewards.append(float(environmental_reward))
        return environmental_rewards


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
    repo = Path(__file__).resolve().parents[1]
    merged_main = subprocess.check_output(
        ["git", "rev-parse", "origin/main"], cwd=repo, text=True
    ).strip()
    protocol_path = repo / "docs/project/CONTROLLED_G9_ADMISSION_V1.md"
    if (
        receipt.get("profile") != PROFILE
        or receipt.get("approved_by") != "Viraj Choudhary"
        or receipt.get("execution_allowed") is not True
        or receipt.get("spend_usd_max") != 0
        or receipt.get("host") not in {"Kaggle private free T4", "Colab free T4"}
        or source["source_state"] != "clean"
        or source["code_sha"] != merged_main
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


def _require_reward_gradient_diagnostic() -> None:
    if not REWARD_GRADIENT_DIAGNOSTIC_AVAILABLE:
        raise RuntimeError(
            "Controlled GRPO is fail-closed: a separate environmental-verification "
            "policy gradient diagnostic is implemented but has not been validated; "
            "the beta=0.04 total gradient may include KL."
        )


def run_pilot(receipt_path: Path, receipt_sha256: str) -> None:
    """Actual GRPO updates; never called by ordinary unit tests or CI."""
    receipt = admit_execution(receipt_path, receipt_sha256)
    _require_reward_gradient_diagnostic()
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
    _require_reward_gradient_diagnostic()
    import torch
    from datasets import Dataset
    from peft import PeftModel, prepare_model_for_kbit_training
    from transformers import (
        AutoModelForCausalLM,
        AutoTokenizer,
        BitsAndBytesConfig,
        TrainerCallback,
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
        "tokenizer_identity": {"id": MODEL, "revision": REVISION},
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
        reward_gradients = []
        optimizer_steps = []
        reward_learning_groups = []
        previous_step_parameters = {
            name: parameter.detach().cpu().clone()
            for name, parameter in parameters.items()
        }
        environment = CapacityEnvironment(ledger)

        class GradientEvidence(TrainerCallback):
            def on_pre_optimizer_step(self, args, state, control, **kwargs):
                group_index = state.global_step + 1
                if not 1 <= group_index <= len(reward_learning_groups):
                    raise ValueError("GRPO optimizer step has no matched reward group")
                signal = reward_learning_groups[group_index - 1]
                if (
                    len(reward_gradients) != group_index
                    or reward_gradients[-1]["group"] != group_index
                    or not reward_gradients[-1]["finite"]
                    or reward_gradients[-1]["norm"] is None
                    or reward_gradients[-1]["norm"] <= 0
                    or reward_gradients[-1]["environmental_advantages_sha256"]
                    != signal["environmental_advantages_sha256"]
                    or reward_gradients[-1]["environmental_signal_sha256"]
                    != signal["environmental_signal_sha256"]
                    or reward_gradients[-1]["optimization_input_sha256"]
                    != signal["optimization_input_sha256"]
                ):
                    raise ValueError(
                        "GRPO optimizer step has no same-group environmental-verifier gradient"
                    )
                values = [p.grad for p in parameters.values() if p.grad is not None]
                finite = bool(values) and all(torch.isfinite(v).all().item() for v in values)
                norm = sum(float(v.float().norm().item()) for v in values)
                gradients.append(
                    {
                        "step": state.global_step,
                        "group": group_index,
                        "finite": finite,
                        "norm": norm,
                        "environmental_advantages_sha256":
                            signal["environmental_advantages_sha256"],
                        "environmental_signal_sha256":
                            signal["environmental_signal_sha256"],
                        "optimization_input_sha256": signal["optimization_input_sha256"],
                    }
                )
                ledger.append("gradient_observed", gradients[-1])
                if not finite:
                    raise ValueError("Missing/nonfinite GRPO gradients")

            def on_step_end(self, args, state, control, **kwargs):
                group_index = state.global_step
                if not 1 <= group_index <= len(reward_learning_groups):
                    raise ValueError("GRPO optimizer update has no matched reward group")
                signal = reward_learning_groups[group_index - 1]
                changed_trainable_tensors = 0
                for name, parameter in parameters.items():
                    current = parameter.detach().cpu()
                    if not torch.equal(previous_step_parameters[name], current):
                        changed_trainable_tensors += 1
                        previous_step_parameters[name] = current.clone()
                record = {
                    "step": group_index,
                    "group": group_index,
                    "changed_trainable_tensors": changed_trainable_tensors,
                    "environmental_advantages_sha256":
                        signal["environmental_advantages_sha256"],
                    "environmental_signal_sha256":
                        signal["environmental_signal_sha256"],
                    "optimization_input_sha256": signal["optimization_input_sha256"],
                }
                optimizer_steps.append(record)
                ledger.append("optimizer_step_observed", record)

        class RawGeneration(ControlledGRPOMixin, GRPOTrainer):
            def compute_loss(
                self,
                model,
                inputs,
                return_outputs=False,
                num_items_in_batch=None,
            ):
                loss = super().compute_loss(
                    model,
                    inputs,
                    return_outputs=return_outputs,
                    num_items_in_batch=num_items_in_batch,
                )
                if getattr(model, "training", None) is True:
                    record = self.environmental_gradient_evidence[-1]
                    group_index = self.state.global_step + 1
                    if (
                        record["group"] != group_index
                        or not reward_learning_groups
                        or reward_learning_groups[-1]["group"] != group_index
                        or len(reward_gradients) + 1 != group_index
                        or record["environmental_advantages_sha256"]
                        != reward_learning_groups[-1]["environmental_advantages_sha256"]
                        or record["environmental_signal_sha256"]
                        != reward_learning_groups[-1]["environmental_signal_sha256"]
                        or record["optimization_input_sha256"]
                        != reward_learning_groups[-1]["optimization_input_sha256"]
                    ):
                        raise ValueError(
                            "Environmental-verifier gradient does not match the active reward group"
                        )
                    reward_gradients.append(record)
                    ledger.append("reward_gradient_observed", record)
                return loss

            def _generate_and_score_completions(self, inputs):
                result = super()._generate_and_score_completions(inputs)
                observed_advantages = result["advantages"].detach().cpu()
                if not torch.isfinite(observed_advantages).all().item():
                    raise ValueError("GRPO advantages must be finite")
                groups = self.observation_evidence
                if not groups:
                    raise ValueError("GRPO generation has no completed reward group")
                group = groups[-1]
                group_index = len(groups)
                records = group["records"]
                if (
                    group_index != environment.group
                    or group["status"] != "completed"
                    or len(records) != HYPERPARAMETERS["num_generations"]
                ):
                    raise ValueError("GRPO generation does not match its objective group")
                objective_rewards = [record["reward"] for record in records]
                environmental_components = [
                    record["environmental_components"] for record in records
                ]
                environmental_rewards = [
                    record["environmental_reward"] for record in records
                ]
                environmental_admissible = [
                    record["environmental_admissible"] for record in records
                ]
                advantage_values = [
                    float(value)
                    for value in observed_advantages.reshape(-1).tolist()
                ]
                if len(advantage_values) != len(records):
                    raise ValueError(
                        "GRPO environmental advantages have the wrong sample count"
                    )
                if not has_reward_driven_advantages(
                    environmental_rewards, advantage_values
                ):
                    raise ValueError(
                        "GRPO advantages do not match environmental verifier rewards"
                    )
                if not has_environmental_reward_signal(
                    environmental_components,
                    environmental_admissible,
                    advantage_values,
                ):
                    raise ValueError(
                        "GRPO group lacks two admissible samples with varied "
                        "environmental verifier components"
                    )
                group_token = records[0]["g9_group_token"]
                if (
                    not isinstance(group_token, str)
                    or not group_token
                    or any(record["g9_group_token"] != group_token for record in records)
                    or group.get("observation_digest") != records[0]["observation_digest"]
                    or group.get("state_prompt_sha256")
                    != records[0]["state_prompt_sha256"]
                    or any(
                        record["observation_digest"] != group["observation_digest"]
                        or record["state_prompt_sha256"]
                        != group["state_prompt_sha256"]
                        for record in records
                    )
                ):
                    raise ValueError(
                        "GRPO environmental group has inconsistent state or prompt lineage"
                    )
                environmental_advantages_sha256 = _environmental_advantage_sha256(
                    advantage_values
                )
                environmental_signal_sha256 = _environmental_signal_sha256(result)
                optimization_input_sha256 = _optimization_input_sha256(result)
                environment_evidence = {
                    "group": group_index,
                    "group_token": group_token,
                    "environmental_rewards": environmental_rewards,
                    "environmental_components": environmental_components,
                    "environmental_admissible": environmental_admissible,
                    "environmental_advantages": advantage_values,
                    "environmental_advantages_sha256":
                        environmental_advantages_sha256,
                    "environmental_signal_sha256": environmental_signal_sha256,
                    "optimization_input_sha256": optimization_input_sha256,
                    "environmental_signal": True,
                }
                self._environmental_group_evidence.append(environment_evidence)
                reward_learning_groups.append(
                    {
                        **environment_evidence,
                        "objective_rewards": objective_rewards,
                    }
                )
                objective_advantages = normalize_group_advantages(objective_rewards)
                ledger.append(
                    "generation_tokens",
                    {
                        "group": group_index,
                        "prompt_ids": result["prompt_ids"].detach().cpu().tolist(),
                        "completion_ids": result["completion_ids"].detach().cpu().tolist(),
                        "group_token": group_token,
                        "scenario_id": group["scenario_id"],
                        "observation_digest": group["observation_digest"],
                        "state_prompt_sha256": group["state_prompt_sha256"],
                        "completion_sha256s": [
                            record["completion_sha256"] for record in records
                        ],
                        "objective_rewards": objective_rewards,
                        "objective_advantages": objective_advantages,
                        "environmental_components": environmental_components,
                        "environmental_rewards": environmental_rewards,
                        "environmental_admissible": environmental_admissible,
                        "environmental_advantages": advantage_values,
                        "environmental_advantages_sha256":
                            environmental_advantages_sha256,
                        "environmental_signal_sha256":
                            environmental_signal_sha256,
                        "optimization_input_sha256": optimization_input_sha256,
                        "advantages": advantage_values,
                    },
                )
                return result

        config = GRPOConfig(
            output_dir=str(output / "trainer"), **HYPERPARAMETERS,
            fp16=True, bf16=False, use_vllm=False, report_to=[],
            save_strategy="no", logging_steps=1, remove_unused_columns=False,
            dataloader_num_workers=0, optim="paged_adamw_8bit",
        )
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
        expected_groups = set(range(1, HYPERPARAMETERS["max_steps"] + 1))
        reward_signals = {
            group["group"]: group["environmental_signal"]
            for group in reward_learning_groups
        }
        gradients_by_group = {record["group"]: record for record in gradients}
        reward_gradients_by_group = {
            record["group"]: record for record in reward_gradients
        }
        optimizer_steps_by_group = {record["group"]: record for record in optimizer_steps}
        if (
            trainer.state.global_step != HYPERPARAMETERS["max_steps"]
            or len(gradients) != HYPERPARAMETERS["max_steps"]
            or len(reward_gradients) != HYPERPARAMETERS["max_steps"]
            or len(optimizer_steps) != HYPERPARAMETERS["max_steps"]
            or set(reward_signals) != expected_groups
            or set(gradients_by_group) != expected_groups
            or set(reward_gradients_by_group) != expected_groups
            or set(optimizer_steps_by_group) != expected_groups
            or not all(
                reward_signals[group]
                and reward_gradients_by_group[group]["finite"]
                and reward_gradients_by_group[group]["norm"] is not None
                and reward_gradients_by_group[group]["norm"] > 0
                and reward_gradients_by_group[group][
                    "environmental_advantages_sha256"
                ] == reward_learning_groups[group - 1][
                    "environmental_advantages_sha256"
                ]
                and reward_gradients_by_group[group][
                    "environmental_signal_sha256"
                ] == reward_learning_groups[group - 1][
                    "environmental_signal_sha256"
                ]
                and reward_gradients_by_group[group][
                    "optimization_input_sha256"
                ] == reward_learning_groups[group - 1]["optimization_input_sha256"]
                and gradients_by_group[group]["finite"]
                and gradients_by_group[group]["norm"] > 0
                and gradients_by_group[group]["environmental_advantages_sha256"]
                == reward_learning_groups[group - 1][
                    "environmental_advantages_sha256"
                ]
                and gradients_by_group[group]["environmental_signal_sha256"]
                == reward_learning_groups[group - 1][
                    "environmental_signal_sha256"
                ]
                and gradients_by_group[group]["optimization_input_sha256"]
                == reward_learning_groups[group - 1]["optimization_input_sha256"]
                and optimizer_steps_by_group[group]["changed_trainable_tensors"] > 0
                and optimizer_steps_by_group[group][
                    "environmental_advantages_sha256"
                ] == reward_learning_groups[group - 1][
                    "environmental_advantages_sha256"
                ]
                and optimizer_steps_by_group[group][
                    "environmental_signal_sha256"
                ] == reward_learning_groups[group - 1][
                    "environmental_signal_sha256"
                ]
                and optimizer_steps_by_group[group][
                    "optimization_input_sha256"
                ] == reward_learning_groups[group - 1]["optimization_input_sha256"]
                for group in expected_groups
            )
            or not changed
            or not all(torch.isfinite(p).all().item() for p in parameters.values())
        ):
            raise ValueError(
                "GRPO did not establish a same-group environmental-verifier gradient "
                "and parameter update"
            )
        validate_v17_parent(parent)
        _verify_model_inventory(receipt)
        model.save_pretrained(output / "adapter", safe_serialization=True)
        tokenizer.save_pretrained(output / "adapter")
        trainer.save_state()
        trainer._save_optimizer_and_scheduler(str(output / "trainer"))
        ledger.append(
            "training_finished",
            {"global_step": trainer.state.global_step, "changed_tensors": changed,
             "gradients": gradients, "reward_gradients": reward_gradients,
             "optimizer_steps": optimizer_steps,
             "trainer_log_history": trainer.state.log_history,
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
        manifest["evidence_files"] = {
            path.relative_to(output).as_posix(): {
                "bytes": path.stat().st_size,
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            }
            for path in sorted(output.rglob("*"))
            if path.is_file() and path != manifest_path
        }
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
