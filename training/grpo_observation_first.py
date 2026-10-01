"""Disconnected, non-empirical observation-first candidate for pinned TRL GRPO."""

from __future__ import annotations

import copy
import hashlib
import importlib.metadata
import inspect
import json
import secrets
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Protocol

from config.splits import get_split

MAX_PUBLIC_STATE_BYTES = 16_384
_PUBLIC_STATE_FIELDS = frozenset(
    {"alert", "incident_id", "observations", "triage"}
)
_POLICY_STATE_FIELDS = _PUBLIC_STATE_FIELDS | {"instruction"}
_BINDING_FIELDS = (
    "scenario_id",
    "g9_scenario_id",
    "g9_group_token",
    "g9_observation_digest",
    "g9_prompt_sha256",
    "g9_sample_index",
)
_APPROVAL_DECISIONS = frozenset(
    {"approved", "rejected", "timeout", "missing", "identity_missing"}
)


class ObservationLifecycle(Protocol):
    """Required synchronous environment boundary; no default live adapter exists."""

    def begin(self, scenario_id: str) -> Mapping[str, Any]: ...

    def before_action(
        self, snapshot: Mapping[str, Any], index: int
    ) -> Mapping[str, Any]: ...

    def execute(
        self, raw_completion: str, state: Mapping[str, Any], index: int
    ) -> Mapping[str, Any]: ...

    def finish(self, group: Mapping[str, Any], status: str) -> None: ...


@dataclass(frozen=True)
class _GenerationGroup:
    scenario_id: str
    token: str
    snapshot_bytes: bytes
    digest: str
    prompt_sha256: str
    observed_at: str
    group_size: int


def _require_sync(value: Any, operation: str) -> Any:
    if inspect.isawaitable(value):
        close = getattr(value, "close", None)
        if callable(close):
            close()
        raise TypeError(f"Observation lifecycle {operation} must be synchronous")
    return value


def _utc_timestamp(value: Any, field_name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Observation lifecycle {field_name} must be an RFC 3339 timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(
            f"Observation lifecycle {field_name} must be an RFC 3339 timestamp"
        ) from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError(f"Observation lifecycle {field_name} must include a timezone")
    return parsed.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _public_snapshot(state: Any) -> tuple[bytes, str]:
    if not isinstance(state, Mapping):
        raise TypeError("Observed policy state must be an object")
    if any(not isinstance(key, str) for key in state):
        raise ValueError("Observed policy state field names must be strings")
    unknown = set(state) - _POLICY_STATE_FIELDS
    if unknown:
        raise ValueError(
            "Observed policy state contains unsupported top-level fields: "
            f"{sorted(unknown)}"
        )
    from bench.grpo_eval import ACTION_INSTRUCTION, _public_policy_state

    if "instruction" in state and state["instruction"] != ACTION_INSTRUCTION:
        raise ValueError("Observed policy state cannot override the fixed instruction")
    if not isinstance(state.get("alert"), Mapping):
        raise ValueError("Observed policy state requires an alert object")
    for key in ("triage", "observations"):
        if key in state and not isinstance(state[key], Mapping):
            raise ValueError(f"Observed policy state {key} must be an object")
    if "incident_id" in state and (
        not isinstance(state["incident_id"], str) or not state["incident_id"].strip()
    ):
        raise ValueError("Observed policy state incident_id must be non-empty text")

    public_state = _public_policy_state(state)
    snapshot_bytes = json.dumps(
        public_state,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")
    if len(snapshot_bytes) > MAX_PUBLIC_STATE_BYTES:
        raise ValueError(
            f"Observed policy state exceeds {MAX_PUBLIC_STATE_BYTES} bytes"
        )
    return snapshot_bytes, hashlib.sha256(snapshot_bytes).hexdigest()


def _fresh_state(snapshot_bytes: bytes) -> dict[str, Any]:
    return json.loads(snapshot_bytes.decode("utf-8"))


def _column_values(
    value: Any, *, name: str, expected_count: int
) -> list[Any]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(f"GRPO reward binding {name} must be a sequence")
    values = list(value)
    if len(values) != expected_count:
        raise ValueError(f"GRPO reward binding {name} has the wrong group size")
    return values


def _action_lineage_matches(
    result: Mapping[str, Any],
    *,
    completion: str,
    action: Mapping[str, Any],
    scenario_id: str,
) -> bool:
    if result.get("scenario_id") != scenario_id:
        return False
    if result.get("policy_completion") != completion:
        return False
    if result.get("policy_action") != action:
        return False
    if result.get("agent_claimed_resolved") is not action["agent_claimed_resolved"]:
        return False
    executed = result.get("executed_actions")
    if result.get("status") == "blocked":
        return isinstance(executed, list) and not executed
    if not isinstance(executed, list) or len(executed) != 1:
        return False
    action_result = executed[0]
    return (
        isinstance(action_result, Mapping)
        and action_result.get("tool") == action["tool"]
        and action_result.get("arguments") == action["arguments"]
    )


def _approval_decision(approval: Any) -> str:
    if not isinstance(approval, Mapping) or "decision" not in approval:
        return "unavailable"
    decision = approval.get("decision")
    if isinstance(decision, str) and decision in _APPROVAL_DECISIONS:
        return decision
    return "invalid"


class ObservationFirstGRPOMixin:
    """Admit one observed Train group before pinned TRL performs generation.

    This candidate deliberately supports exactly one complete generation group per
    call. It is disconnected from production training and all environment behavior
    is supplied explicitly through ``observation_lifecycle``.
    """

    def __init__(
        self,
        *args: Any,
        observation_lifecycle: ObservationLifecycle,
        **kwargs: Any,
    ) -> None:
        self._observation_lifecycle = self._validate_lifecycle(observation_lifecycle)
        self._active_group: _GenerationGroup | None = None
        self._active_records: list[dict[str, Any]] = []
        self._reward_invoked = False
        self._observation_evidence: list[dict[str, Any]] = []

        initializer = super().__init__
        signature = inspect.signature(initializer)
        parameters = list(signature.parameters)
        if "reward_funcs" not in parameters:
            raise TypeError("Pinned TRL trainer must accept reward_funcs")
        reward_position = parameters.index("reward_funcs")
        bound = signature.bind_partial(*args, **kwargs)
        supplied_rewards = bound.arguments.get("reward_funcs")
        if supplied_rewards is not None and not (
            isinstance(supplied_rewards, (list, tuple)) and not supplied_rewards
        ):
            raise ValueError(
                "Observation-first trainer owns the only reward function"
            )
        if len(args) > reward_position:
            args = list(args)
            if args[reward_position] is not None and args[reward_position] != []:
                raise ValueError(
                    "Observation-first trainer owns the only reward function"
                )
            args[reward_position] = [self._observation_reward]
            args = tuple(args)
            kwargs.pop("reward_funcs", None)
        else:
            kwargs["reward_funcs"] = [self._observation_reward]

        config = bound.arguments.get("args")
        if config is None or not hasattr(config, "max_prompt_length"):
            raise ValueError(
                "Observation-first candidate requires an explicit GRPOConfig "
                "with max_prompt_length=None"
            )
        if config.max_prompt_length is not None:
            raise ValueError(
                "Observation-first candidate requires max_prompt_length=None; "
                "pinned TRL otherwise truncates the observed policy prompt"
            )

        initializer(*args, **kwargs)
        if not hasattr(self, "max_prompt_length") or self.max_prompt_length is not None:
            raise ValueError(
                "Observation-first candidate requires max_prompt_length=None; "
                "pinned TRL otherwise truncates the observed policy prompt"
            )

    @staticmethod
    def _validate_lifecycle(lifecycle: ObservationLifecycle) -> ObservationLifecycle:
        for name in ("begin", "before_action", "execute", "finish"):
            if not callable(getattr(lifecycle, name, None)):
                raise TypeError(f"Observation lifecycle requires callable {name}")
        return lifecycle

    @property
    def observation_evidence(self) -> tuple[dict[str, Any], ...]:
        """Return a defensive read-only view of local NON_EMPIRICAL records."""
        return tuple(copy.deepcopy(self._observation_evidence))

    def _validate_generation_batch(
        self, inputs: Any
    ) -> tuple[str, int]:
        if self.max_prompt_length is not None:
            raise ValueError(
                "Observation-first candidate requires max_prompt_length=None"
            )
        count = self.num_generations
        if isinstance(count, bool) or not isinstance(count, int) or count <= 0:
            raise ValueError("num_generations must be a positive integer")
        if not isinstance(inputs, list):
            raise TypeError("Pinned TRL generation inputs must be a list")
        if len(inputs) != count:
            raise ValueError(
                "This observation-first candidate supports exactly one complete "
                "generation group per batch; multiple groups are unsupported by "
                "this candidate, not a project budget decision"
            )

        scenario_ids: list[str] = []
        for row in inputs:
            if not isinstance(row, Mapping):
                raise TypeError("Each GRPO generation input must be an object")
            prompt = row.get("prompt")
            scenario_id = row.get("scenario_id")
            if not isinstance(prompt, str):
                raise TypeError("Each GRPO generation input requires a text prompt")
            if not isinstance(scenario_id, str) or not scenario_id:
                raise ValueError("Each GRPO generation input requires scenario_id")
            scenario_ids.append(scenario_id)

        scenario_id = scenario_ids[0]
        if any(value != scenario_id for value in scenario_ids):
            raise ValueError("A generation group must bind to one scenario_id")
        if scenario_id not in set(get_split("train")):
            raise ValueError(
                f"Observation-first GRPO scenario is outside frozen Train: {scenario_id}"
            )
        return scenario_id, count

    def _generate_and_score_completions(self, inputs: Any) -> Any:
        if self._active_group is not None:
            raise RuntimeError("Observation-first GRPO group is already active")
        scenario_id, group_size = self._validate_generation_batch(inputs)
        token = secrets.token_hex(16)
        begin_attempted = False
        group: _GenerationGroup | None = None
        status = "failed"
        error_type: str | None = None
        active_error: BaseException | None = None

        try:
            begin_attempted = True
            observed = _require_sync(
                self._observation_lifecycle.begin(scenario_id), "begin"
            )
            if not isinstance(observed, Mapping) or set(observed) != {
                "state",
                "observed_at",
            }:
                raise ValueError(
                    "Observation lifecycle begin must return state and observed_at"
                )
            snapshot_bytes, digest = _public_snapshot(observed["state"])
            observed_at = _utc_timestamp(observed["observed_at"], "observed_at")
            prompt = snapshot_bytes.decode("utf-8")
            prompt_sha256 = hashlib.sha256(prompt.encode("utf-8")).hexdigest()
            group = _GenerationGroup(
                scenario_id=scenario_id,
                token=token,
                snapshot_bytes=snapshot_bytes,
                digest=digest,
                prompt_sha256=prompt_sha256,
                observed_at=observed_at,
                group_size=group_size,
            )
            self._active_group = group
            self._active_records = []
            self._reward_invoked = False

            generation_inputs = []
            for sample_index in range(group_size):
                generation_inputs.append(
                    {
                        "prompt": prompt,
                        "scenario_id": scenario_id,
                        "g9_scenario_id": scenario_id,
                        "g9_group_token": token,
                        "g9_observation_digest": digest,
                        "g9_prompt_sha256": prompt_sha256,
                        "g9_sample_index": sample_index,
                    }
                )

            result = super()._generate_and_score_completions(generation_inputs)
            if not self._reward_invoked:
                raise RuntimeError(
                    "Pinned TRL generation hook returned without its bound reward callback"
                )
            status = "completed"
            return result
        except BaseException as exc:
            active_error = exc
            error_type = type(exc).__name__
            status = (
                "interrupted"
                if isinstance(exc, (KeyboardInterrupt, GeneratorExit))
                or type(exc).__name__ == "CancelledError"
                else "failed"
            )
            raise
        finally:
            if begin_attempted:
                summary = self._group_summary(
                    group=group,
                    scenario_id=scenario_id,
                    status=status,
                    error_type=error_type,
                    records=self._active_records,
                )
                try:
                    _require_sync(
                        self._observation_lifecycle.finish(
                            copy.deepcopy(summary), status
                        ),
                        "finish",
                    )
                except BaseException as finish_error:
                    if status == "completed":
                        status = "failed"
                    summary["status"] = status
                    summary["finish_callback_status"] = "raised"
                    summary["finish_error_type"] = type(finish_error).__name__
                    if error_type is None:
                        summary["error_type"] = type(finish_error).__name__
                    self._observation_evidence.append(copy.deepcopy(summary))
                    if active_error is None:
                        raise
                    active_error.add_note(
                        "Observation lifecycle finish also failed: "
                        f"{type(finish_error).__name__}"
                    )
                else:
                    summary["finish_callback_status"] = "returned"
                    self._observation_evidence.append(copy.deepcopy(summary))
                finally:
                    self._active_group = None
                    self._active_records = []
                    self._reward_invoked = False

    def _group_summary(
        self,
        *,
        group: _GenerationGroup | None,
        scenario_id: str,
        status: str,
        error_type: str | None,
        records: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "scenario_id": scenario_id,
            "observation_digest": group.digest if group else None,
            "state_prompt_sha256": group.prompt_sha256 if group else None,
            "observed_at": group.observed_at if group else None,
            "generation_count": group.group_size if group else self.num_generations,
            "status": status,
            "error_type": error_type,
            "records": copy.deepcopy(records),
            "result_classification": "NON_EMPIRICAL",
            "certification_status": "NOT_CERTIFIED",
        }

    def _observation_reward(
        self,
        *,
        prompts: Any,
        completions: Any,
        **kwargs: Any,
    ) -> list[float]:
        group = self._active_group
        if group is None:
            raise RuntimeError("Observation-first reward callback has no active group")
        if self._reward_invoked:
            raise RuntimeError("Observation-first reward callback is one-shot per group")
        self._reward_invoked = True

        count = group.group_size
        prompt_values = _column_values(
            prompts, name="prompt", expected_count=count
        )
        completion_values = _column_values(
            completions, name="completion", expected_count=count
        )
        bindings = {
            name: _column_values(
                kwargs.get(name), name=name, expected_count=count
            )
            for name in _BINDING_FIELDS
        }
        expected_prompt = group.snapshot_bytes.decode("utf-8")
        expected_values = {
            "scenario_id": group.scenario_id,
            "g9_scenario_id": group.scenario_id,
            "g9_group_token": group.token,
            "g9_observation_digest": group.digest,
            "g9_prompt_sha256": group.prompt_sha256,
        }
        if any(value != expected_prompt for value in prompt_values):
            raise ValueError("Generated prompts no longer match the observed snapshot")
        for name, values in bindings.items():
            if name == "g9_sample_index":
                if values != list(range(count)):
                    raise ValueError(
                        "GRPO reward binding g9_sample_index was reordered"
                    )
            elif any(value != expected_values[name] for value in values):
                raise ValueError(f"GRPO reward binding {name} does not match its group")
        if any(not isinstance(value, str) for value in completion_values):
            raise TypeError("All GRPO completions must be raw strings before execution")

        from training.grpo_environment import parse_policy_action

        actions = [parse_policy_action(value) for value in completion_values]
        rewards = []
        for index, (completion, action) in enumerate(
            zip(completion_values, actions, strict=True)
        ):
            record: dict[str, Any] = {
                "scenario_id": group.scenario_id,
                "sample_index": index,
                "observation_digest": group.digest,
                "state_prompt_sha256": group.prompt_sha256,
                "observed_at": group.observed_at,
                "before_action_observed_at": None,
                "completion_sha256": hashlib.sha256(
                    completion.encode("utf-8")
                ).hexdigest(),
                "action": copy.deepcopy(action),
                "result_status": None,
                "verifier_status": None,
                "terminal_block": None,
                "approval_decision": None,
                "reward": None,
                "scorable": False,
                "result_classification": "NON_EMPIRICAL",
                "certification_status": "NOT_CERTIFIED",
            }
            self._active_records.append(record)
            try:
                admitted = _require_sync(
                    self._observation_lifecycle.before_action(
                        _fresh_state(group.snapshot_bytes), index
                    ),
                    "before_action",
                )
            except BaseException as exc:
                record["failure"] = f"before_action_exception:{type(exc).__name__}"
                raise
            if not isinstance(admitted, Mapping) or set(admitted) != {
                "state",
                "observed_at",
            }:
                record["failure"] = "invalid_before_action_observation"
                raise ValueError(
                    "Observation lifecycle before_action must return state and observed_at"
                )
            try:
                admitted_bytes, admitted_digest = _public_snapshot(admitted["state"])
            except (TypeError, ValueError) as exc:
                record["failure"] = f"before_action_state_invalid:{type(exc).__name__}"
                raise
            if admitted_digest != group.digest or admitted_bytes != group.snapshot_bytes:
                record["failure"] = "before_action_state_drift"
                raise ValueError(
                    "Fresh pre-action state drifted from the generation snapshot"
                )
            try:
                before_action_at = _utc_timestamp(
                    admitted["observed_at"], "before_action observed_at"
                )
            except (TypeError, ValueError):
                record["failure"] = "before_action_timestamp_invalid"
                raise
            if datetime.fromisoformat(
                before_action_at.replace("Z", "+00:00")
            ) < datetime.fromisoformat(group.observed_at.replace("Z", "+00:00")):
                record["failure"] = "before_action_timestamp_invalid"
                raise ValueError("Fresh pre-action observation predates the generation snapshot")
            record["before_action_observed_at"] = before_action_at
            try:
                result = _require_sync(
                    self._observation_lifecycle.execute(
                        completion, _fresh_state(admitted_bytes), index
                    ),
                    "execute",
                )
            except BaseException as exc:
                record["failure"] = f"execute_exception:{type(exc).__name__}"
                raise
            if not isinstance(result, Mapping):
                record["failure"] = "invalid_environment_result"
                raise TypeError("Observation lifecycle execute must return an object")
            result = dict(result)
            record["result_status"] = (
                result.get("status") if isinstance(result.get("status"), str) else None
            )
            verification = result.get("verification")
            record["verifier_status"] = (
                verification.get("verification_status")
                if isinstance(verification, Mapping)
                and isinstance(verification.get("verification_status"), str)
                else None
            )
            if not _action_lineage_matches(
                result,
                completion=completion,
                action=action,
                scenario_id=group.scenario_id,
            ):
                record["failure"] = "action_lineage_mismatch"
                raise ValueError(
                    "Environment result does not match the exact policy completion/action"
                )
            if result.get("status") == "blocked":
                from training.grpo_environment import TERMINAL_BLOCK_CATEGORIES

                terminal_block = result.get("terminal_block")
                category = (
                    terminal_block.get("category")
                    if isinstance(terminal_block, Mapping)
                    else None
                )
                if not isinstance(category, str) or category not in TERMINAL_BLOCK_CATEGORIES:
                    record["failure"] = "invalid_blocked_result"
                    raise ValueError(
                        "Environment blocked result has an invalid terminal category"
                    )
                record["terminal_block"] = {"category": category}
                if category == "approval_required":
                    record["approval_decision"] = _approval_decision(
                        result.get("approval")
                    )
                record["failure"] = f"environment_blocked:{category}"
                raise ValueError(f"Environment blocked policy action: {category}")
            try:
                from training.grpo import compute_direct_action_reward

                reward = compute_direct_action_reward(result)
            except BaseException as exc:
                record["failure"] = type(exc).__name__
                raise
            record["reward"] = reward
            record["scorable"] = True
            rewards.append(reward)
        return rewards


def make_trl_0191_observation_first_trainer() -> type:
    """Load only the exact pinned TRL hook after verifying its installed metadata."""
    try:
        installed = importlib.metadata.version("trl")
    except importlib.metadata.PackageNotFoundError as exc:
        raise RuntimeError("TRL 0.19.1 is required for the observation-first hook") from exc
    if installed != "0.19.1":
        raise RuntimeError(
            f"Observation-first hook requires TRL 0.19.1, found {installed}"
        )

    from trl import GRPOTrainer

    return type(
        "TRL0191ObservationFirstGRPOTrainer",
        (ObservationFirstGRPOMixin, GRPOTrainer),
        {},
    )
