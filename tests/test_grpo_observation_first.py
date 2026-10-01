from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from config.splits import get_split
from training import grpo_observation_first as observation_first
from training.grpo_environment import parse_policy_action
from training.grpo_observation_first import ObservationFirstGRPOMixin


SCENARIO_ID = "single_fault/sf-002"
COMPLETION = (
    '{"tool":"kubectl_get","arguments":{},"agent_claimed_resolved":false}'
)


def _result(completion: str) -> dict:
    action = parse_policy_action(completion)
    return {
        "scenario_id": SCENARIO_ID,
        "status": "ok",
        "scorable": True,
        "settling": {
            "status": "settled",
            "stable": True,
            "stable_observations": 2,
        },
        "policy_completion": completion,
        "policy_action": action,
        "executed_actions": [
            {"tool": action["tool"], "arguments": action["arguments"], "result": {}}
        ],
        "verification": {
            "verification_status": "failed",
            "env_resolved": False,
            "checks": [
                {"name": "workload_ready", "required": True, "passed": True},
                {"name": "chaos_cleared", "required": True, "passed": False},
            ],
        },
        "agent_claimed_resolved": False,
    }


def _blocked_result(
    completion: str, category: str, approval_decision: str | None = None
) -> dict:
    action = parse_policy_action(completion)
    result = {
        "scenario_id": SCENARIO_ID,
        "status": "blocked",
        "scorable": True,
        "policy_completion": completion,
        "policy_action": action,
        "executed_actions": [],
        "verification": None,
        "agent_claimed_resolved": action["agent_claimed_resolved"],
        "terminal_block": {
            "category": category,
            "reason": "private environment detail",
        },
    }
    if approval_decision is not None:
        result["approval"] = {
            "decision": approval_decision,
            "approved_by": "private operator identity",
            "reason": "private approval detail",
            "token": "private approval token",
        }
    return result


class _Lifecycle:
    def __init__(self):
        self.events = []
        self.finished = []
        self.before_snapshots = []
        self.begin_error = None
        self.before_error = None
        self.before_error_index = None
        self.before_state = None
        self.before_observed_at = None
        self.mutate_before_snapshot = False
        self.execute_error = None
        self.result_mutator = None
        self.finish_error = None
        self.state = {
            "incident_id": "observed-incident-1",
            "alert": {"labels": {"alertname": "ObservedCpu"}, "status": "firing"},
            "triage": {"severity": "P2"},
            "observations": {"ready_replicas": 1},
        }

    def begin(self, scenario_id):
        self.events.append(("begin", scenario_id))
        if self.begin_error is not None:
            raise self.begin_error
        return {"state": self.state, "observed_at": "2026-10-01T00:00:00Z"}

    def before_action(self, snapshot, index):
        self.events.append(("before_action", index))
        self.before_snapshots.append(snapshot)
        if self.before_error is not None and (
            self.before_error_index is None or self.before_error_index == index
        ):
            raise self.before_error
        if self.mutate_before_snapshot:
            snapshot["alert"]["labels"]["alertname"] = "MutatedCallbackCopy"
        state = self.before_state if self.before_state is not None else snapshot
        if self.mutate_before_snapshot and self.before_state is None:
            state = self.state
        return {
            "state": state,
            "observed_at": (
                self.before_observed_at
                or f"2026-10-01T00:00:0{index + 1}Z"
            ),
        }

    def execute(self, raw_completion, state, index):
        self.events.append(("execute", index, raw_completion))
        if self.execute_error is not None:
            raise self.execute_error
        result = _result(raw_completion)
        if self.result_mutator is not None:
            result = self.result_mutator(result)
        return result

    def finish(self, group, status):
        self.finished.append((group, status))
        self.events.append(("finish", status))
        if self.finish_error is not None:
            raise self.finish_error


class _PinnedTrainerDouble:
    def __init__(self, *, model, reward_funcs, args, train_dataset):
        self.model = model
        self.reward_funcs = reward_funcs
        self.args = args
        self.num_generations = args.num_generations
        self.max_prompt_length = args.max_prompt_length
        self.train_dataset = train_dataset
        self.generated_inputs = None
        self.generated_completions = None
        self.reward_input_mutator = None
        self.generation_error = None
        self.invoke_reward_twice = False

    def _generate_and_score_completions(self, inputs):
        if self.generation_error is not None:
            raise self.generation_error
        self.generated_inputs = [dict(row) for row in inputs]
        self._observation_lifecycle.events.append(
            ("generate", [row["prompt"] for row in inputs])
        )
        prompts = [row["prompt"] for row in inputs]
        completions = (
            [COMPLETION] * len(inputs)
            if self.generated_completions is None
            else self.generated_completions
        )
        reward_kwargs = {
            "scenario_id": [row["scenario_id"] for row in inputs],
            "g9_scenario_id": [row["g9_scenario_id"] for row in inputs],
            "g9_group_token": [row["g9_group_token"] for row in inputs],
            "g9_observation_digest": [
                row["g9_observation_digest"] for row in inputs
            ],
            "g9_prompt_sha256": [row["g9_prompt_sha256"] for row in inputs],
            "g9_sample_index": [row["g9_sample_index"] for row in inputs],
        }
        if self.reward_input_mutator is not None:
            prompts, completions, reward_kwargs = self.reward_input_mutator(
                prompts, completions, reward_kwargs
            )
        rewards = self.reward_funcs[0](
            prompts=prompts,
            completions=completions,
            **reward_kwargs,
        )
        if self.invoke_reward_twice:
            self.reward_funcs[0](
                prompts=prompts,
                completions=completions,
                **reward_kwargs,
            )
        return {"completions": completions, "rewards": rewards}


class _Trainer(ObservationFirstGRPOMixin, _PinnedTrainerDouble):
    pass


def _inputs(scenario_ids=None):
    scenario_ids = scenario_ids or [SCENARIO_ID, SCENARIO_ID]
    return [
        {
            "prompt": "stale catalogue prompt",
            "scenario_id": scenario_id,
            "expected_action": "must-not-reach-generation-or-reward",
        }
        for scenario_id in scenario_ids
    ]


def _new_trainer(lifecycle=None, *, max_prompt_length=None):
    lifecycle = lifecycle or _Lifecycle()
    trainer = _Trainer(
        model=SimpleNamespace(training=True),
        args=SimpleNamespace(
            max_prompt_length=max_prompt_length,
            num_generations=2,
        ),
        train_dataset=[],
        observation_lifecycle=lifecycle,
    )
    return trainer, lifecycle


def test_generation_is_conditioned_on_observation_and_scores_exact_executed_action():
    trainer, lifecycle = _new_trainer()
    result = trainer._generate_and_score_completions(_inputs())

    generated_prompts = [row["prompt"] for row in trainer.generated_inputs]
    expected_state = json.loads(generated_prompts[0])
    assert expected_state["alert"]["labels"]["alertname"] == "ObservedCpu"
    assert all(prompt == generated_prompts[0] for prompt in generated_prompts)
    assert all(prompt != "stale catalogue prompt" for prompt in generated_prompts)
    assert all(
        "expected_action" not in row for row in trainer.generated_inputs
    )
    assert result["rewards"] == [0.125, 0.125]
    assert len(trainer.reward_funcs) == 1
    assert trainer.reward_funcs[0].__self__ is trainer
    assert len({row["g9_group_token"] for row in trainer.generated_inputs}) == 1
    assert len(
        {row["g9_observation_digest"] for row in trainer.generated_inputs}
    ) == 1
    assert [event[0] for event in lifecycle.events] == [
        "begin",
        "generate",
        "before_action",
        "execute",
        "before_action",
        "execute",
        "finish",
    ]
    assert [
        event[2] for event in lifecycle.events if event[0] == "execute"
    ] == [COMPLETION, COMPLETION]
    assert lifecycle.finished[0][1] == "completed"
    assert lifecycle.finished[0][0]["result_classification"] == "NON_EMPIRICAL"
    assert lifecycle.finished[0][0]["certification_status"] == "NOT_CERTIFIED"
    assert [record["reward"] for record in trainer.observation_evidence[0]["records"]] == [
        0.125,
        0.125,
    ]


def test_batch_shape_and_scenario_are_rejected_before_lifecycle_begin():
    trainer, lifecycle = _new_trainer()
    with pytest.raises(ValueError, match="one complete generation group"):
        trainer._generate_and_score_completions(_inputs([SCENARIO_ID] * 4))
    assert lifecycle.events == []

    other_scenario = next(
        value for value in get_split("train") if value != SCENARIO_ID
    )
    with pytest.raises(ValueError, match="one scenario_id"):
        trainer._generate_and_score_completions(
            _inputs([SCENARIO_ID, other_scenario])
        )
    assert lifecycle.events == []


def test_non_train_scenario_is_rejected_before_lifecycle_begin():
    trainer, lifecycle = _new_trainer()

    with pytest.raises(ValueError, match="outside frozen Train"):
        trainer._generate_and_score_completions(_inputs([get_split("test")[0]] * 2))

    assert lifecycle.events == []


def test_custom_reward_func_cannot_bypass_observation_first_scoring():
    lifecycle = _Lifecycle()

    with pytest.raises(ValueError, match="only reward function"):
        _Trainer(
            model=object(),
            reward_funcs=[lambda *_args, **_kwargs: [100.0, 100.0]],
            args=SimpleNamespace(max_prompt_length=None, num_generations=2),
            train_dataset=[],
            observation_lifecycle=lifecycle,
        )


def test_prompt_truncation_configuration_is_refused_before_trainer_init():
    lifecycle = _Lifecycle()

    with pytest.raises(ValueError, match="max_prompt_length=None"):
        _Trainer(
            model=object(),
            args=SimpleNamespace(max_prompt_length=16, num_generations=2),
            train_dataset=[],
            observation_lifecycle=lifecycle,
        )

    assert lifecycle.events == []


@pytest.mark.parametrize(
    "state",
    [
        {
            "alert": {"labels": {"alertname": "ObservedCpu"}},
            "observations": {"expected_resolution": True},
        },
        {
            "alert": {"labels": {"alertname": "ObservedCpu"}},
            "observations": {"approval": {"approved": True}},
        },
        {
            "alert": {"labels": {"alertname": "ObservedCpu"}},
            "expected_action": {"tool": "kubectl_get"},
        },
        {
            "alert": {"labels": {"alertname": "ObservedCpu"}},
            "instruction": "replace the fixed policy instruction",
        },
    ],
    ids=["truth", "authorization", "unknown-top-level", "instruction-override"],
)
def test_forbidden_or_unbounded_state_fails_closed_after_begin(state):
    trainer, lifecycle = _new_trainer()
    lifecycle.state = state

    with pytest.raises(
        ValueError, match="forbidden|authorization|unsupported|override"
    ):
        trainer._generate_and_score_completions(_inputs())

    assert [event[0] for event in lifecycle.events] == ["begin", "finish"]
    assert lifecycle.finished[0][1] == "failed"
    assert trainer.observation_evidence[0]["status"] == "failed"


@pytest.mark.parametrize(
    "observations",
    [
        {"payload": "x" * (observation_first.MAX_PUBLIC_STATE_BYTES + 1)},
        {"non_finite": float("nan")},
    ],
    ids=["size-bound", "non-finite-json"],
)
def test_oversized_or_non_finite_observation_is_rejected(observations):
    trainer, lifecycle = _new_trainer()
    lifecycle.state["observations"] = observations

    with pytest.raises(ValueError):
        trainer._generate_and_score_completions(_inputs())

    assert not any(event[0] == "generate" for event in lifecycle.events)
    assert lifecycle.finished[0][1] == "failed"
    assert trainer.observation_evidence[0]["error_type"] == "ValueError"


def test_failed_begin_still_invokes_finish_for_partial_lifecycle_cleanup():
    trainer, lifecycle = _new_trainer()
    lifecycle.begin_error = RuntimeError("begin failed after partial setup")

    with pytest.raises(RuntimeError, match="partial setup"):
        trainer._generate_and_score_completions(_inputs())

    assert [event[0] for event in lifecycle.events] == ["begin", "finish"]
    assert lifecycle.finished[0][1] == "failed"
    assert trainer.observation_evidence[0]["observation_digest"] is None


def test_pre_action_state_drift_blocks_execution():
    trainer, lifecycle = _new_trainer()
    lifecycle.before_state = {
        **lifecycle.state,
        "alert": {"labels": {"alertname": "DifferentObservedAlert"}},
    }

    with pytest.raises(ValueError, match="state drifted"):
        trainer._generate_and_score_completions(_inputs())

    assert [event[0] for event in lifecycle.events] == [
        "begin",
        "generate",
        "before_action",
        "finish",
    ]
    record = trainer.observation_evidence[0]["records"][0]
    assert record["failure"] == "before_action_state_drift"
    assert record["before_action_observed_at"] is None
    assert record["reward"] is None
    assert record["scorable"] is False


def test_pre_action_timestamp_cannot_predate_generation_snapshot():
    trainer, lifecycle = _new_trainer()
    lifecycle.before_observed_at = "2026-09-30T23:59:59Z"

    with pytest.raises(ValueError, match="predates"):
        trainer._generate_and_score_completions(_inputs())

    assert not any(event[0] == "execute" for event in lifecycle.events)
    assert lifecycle.finished[0][1] == "failed"
    record = trainer.observation_evidence[0]["records"][0]
    assert record["failure"] == "before_action_timestamp_invalid"
    assert record["before_action_observed_at"] is None
    assert record["reward"] is None


def test_pre_action_callback_exception_retains_bounded_negative_evidence():
    trainer, lifecycle = _new_trainer()
    lifecycle.before_error = RuntimeError("private callback error")

    with pytest.raises(RuntimeError, match="private callback error"):
        trainer._generate_and_score_completions(_inputs())

    record = trainer.observation_evidence[0]["records"][0]
    assert record["failure"] == "before_action_exception:RuntimeError"
    assert record["completion_sha256"]
    assert record["action"]["tool"] == "kubectl_get"
    assert record["before_action_observed_at"] is None
    assert record["reward"] is None
    assert record["scorable"] is False
    evidence = json.dumps(trainer.observation_evidence[0])
    assert "private callback error" not in evidence


def test_malformed_pre_action_timestamp_retains_negative_evidence():
    trainer, lifecycle = _new_trainer()
    lifecycle.before_observed_at = "not-a-timestamp"

    with pytest.raises(ValueError, match="RFC 3339"):
        trainer._generate_and_score_completions(_inputs())

    record = trainer.observation_evidence[0]["records"][0]
    assert record["failure"] == "before_action_timestamp_invalid"
    assert record["before_action_observed_at"] is None
    assert record["reward"] is None
    assert record["scorable"] is False


def test_completion_strings_are_all_admitted_before_any_execution():
    trainer, lifecycle = _new_trainer()
    trainer.generated_completions = [COMPLETION, "not a policy action"]

    with pytest.raises(ValueError, match="JSON object"):
        trainer._generate_and_score_completions(_inputs())

    assert not any(event[0] == "execute" for event in lifecycle.events)
    assert not any(event[0] == "before_action" for event in lifecycle.events)
    assert lifecycle.finished[0][1] == "failed"


def test_reward_callback_is_one_shot_per_generation_group():
    trainer, lifecycle = _new_trainer()
    trainer.invoke_reward_twice = True

    with pytest.raises(RuntimeError, match="one-shot"):
        trainer._generate_and_score_completions(_inputs())

    assert sum(event[0] == "execute" for event in lifecycle.events) == 2
    assert lifecycle.finished[0][1] == "failed"


@pytest.mark.parametrize(
    "binding",
    ["prompt", "g9_group_token", "g9_observation_digest", "g9_sample_index"],
)
def test_reward_binding_tampering_fails_before_execution(binding):
    trainer, lifecycle = _new_trainer()

    def tamper(prompts, completions, kwargs):
        if binding == "prompt":
            prompts[0] = "altered prompt"
        elif binding == "g9_sample_index":
            kwargs[binding].reverse()
        else:
            kwargs[binding][0] = "altered binding"
        return prompts, completions, kwargs

    trainer.reward_input_mutator = tamper
    with pytest.raises(ValueError, match="match|reordered"):
        trainer._generate_and_score_completions(_inputs())

    assert not any(event[0] == "execute" for event in lifecycle.events)
    assert lifecycle.finished[0][1] == "failed"


@pytest.mark.parametrize("mutation", ["completion", "policy_action", "executed_action"])
def test_environment_must_report_the_exact_completion_and_executed_action(mutation):
    trainer, lifecycle = _new_trainer()

    def tamper(result):
        if mutation == "completion":
            result["policy_completion"] = (
                '{"tool":"kubectl_delete","arguments":{},'
                '"agent_claimed_resolved":false}'
            )
        elif mutation == "policy_action":
            result["policy_action"] = {
                "tool": "kubectl_delete",
                "arguments": {},
                "agent_claimed_resolved": False,
            }
        else:
            result["executed_actions"][0]["tool"] = "kubectl_delete"
        return result

    lifecycle.result_mutator = tamper

    with pytest.raises(ValueError, match="exact policy completion/action"):
        trainer._generate_and_score_completions(_inputs())

    record = trainer.observation_evidence[0]["records"][0]
    assert record["scorable"] is False
    assert record["failure"] == "action_lineage_mismatch"
    assert record["result_classification"] == "NON_EMPIRICAL"


def test_unscorable_environment_result_is_retained_without_invented_reward():
    trainer, lifecycle = _new_trainer()
    lifecycle.result_mutator = lambda result: {**result, "scorable": False}

    with pytest.raises(ValueError, match="unscorable"):
        trainer._generate_and_score_completions(_inputs())

    record = trainer.observation_evidence[0]["records"][0]
    assert record["reward"] is None
    assert record["scorable"] is False
    assert record["failure"] == "ValueError"


@pytest.mark.parametrize(
    "decision", ["approved", "rejected", "timeout", "missing", "identity_missing"]
)
def test_approval_block_preserves_bounded_decision_without_numeric_reward(decision):
    trainer, lifecycle = _new_trainer()
    lifecycle.result_mutator = lambda result: _blocked_result(
        result["policy_completion"], "approval_required", decision
    )

    with pytest.raises(ValueError, match="approval_required"):
        trainer._generate_and_score_completions(_inputs())

    record = trainer.observation_evidence[0]["records"][0]
    assert record["result_status"] == "blocked"
    assert record["terminal_block"] == {"category": "approval_required"}
    assert record["approval_decision"] == decision
    assert record["failure"] == "environment_blocked:approval_required"
    assert record["reward"] is None
    assert record["scorable"] is False
    assert record["result_classification"] == "NON_EMPIRICAL"
    assert lifecycle.finished[0][1] == "failed"
    evidence = json.dumps(trainer.observation_evidence[0])
    for private_value in (
        "private environment detail",
        "private operator identity",
        "private approval detail",
        "private approval token",
    ):
        assert private_value not in evidence


def test_policy_block_is_retained_without_numeric_reward():
    trainer, lifecycle = _new_trainer()
    lifecycle.result_mutator = lambda result: _blocked_result(
        result["policy_completion"], "policy_block"
    )

    with pytest.raises(ValueError, match="policy_block"):
        trainer._generate_and_score_completions(_inputs())

    record = trainer.observation_evidence[0]["records"][0]
    assert record["result_status"] == "blocked"
    assert record["terminal_block"] == {"category": "policy_block"}
    assert record["failure"] == "environment_blocked:policy_block"
    assert record["reward"] is None
    assert record["scorable"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "unexpected_execution",
        "completion",
        "policy_action",
        "scenario",
        "terminal_block",
    ],
)
def test_malformed_blocked_result_is_not_recorded_as_a_valid_environment_block(
    mutation,
):
    trainer, lifecycle = _new_trainer()

    def tamper(_result):
        result = _blocked_result(
            COMPLETION, "approval_required", "rejected"
        )
        if mutation == "unexpected_execution":
            action = parse_policy_action(COMPLETION)
            result["executed_actions"] = [
                {"tool": action["tool"], "arguments": action["arguments"]}
            ]
        elif mutation == "completion":
            result["policy_completion"] = (
                '{"tool":"kubectl_delete","arguments":{},'
                '"agent_claimed_resolved":false}'
            )
        elif mutation == "policy_action":
            result["policy_action"] = {
                "tool": "kubectl_delete",
                "arguments": {},
                "agent_claimed_resolved": False,
            }
        elif mutation == "scenario":
            result["scenario_id"] = "different-scenario"
        else:
            result["terminal_block"] = {"category": "unknown"}
        return result

    lifecycle.result_mutator = tamper
    with pytest.raises(ValueError):
        trainer._generate_and_score_completions(_inputs())

    record = trainer.observation_evidence[0]["records"][0]
    assert record["failure"] in {
        "action_lineage_mismatch",
        "invalid_blocked_result",
    }
    assert record["failure"] != "environment_blocked:approval_required"
    assert record["terminal_block"] is None
    assert record.get("approval_decision") is None
    assert record["reward"] is None
    assert record["scorable"] is False


@pytest.mark.parametrize(
    ("approval", "expected"),
    [
        (None, "unavailable"),
        ({"decision": "untrusted decision", "token": "private token"}, "invalid"),
        ({"decision": None, "approved_by": "private identity"}, "invalid"),
    ],
)
def test_approval_block_does_not_copy_unknown_or_missing_decision_fields(
    approval, expected
):
    trainer, lifecycle = _new_trainer()

    def blocked_with_approval(result):
        blocked = _blocked_result(result["policy_completion"], "approval_required")
        blocked["approval"] = approval
        return blocked

    lifecycle.result_mutator = blocked_with_approval
    with pytest.raises(ValueError, match="approval_required"):
        trainer._generate_and_score_completions(_inputs())

    record = trainer.observation_evidence[0]["records"][0]
    assert record["approval_decision"] == expected
    evidence = json.dumps(trainer.observation_evidence[0])
    assert "untrusted decision" not in evidence
    assert "private token" not in evidence
    assert "private identity" not in evidence


def test_later_pre_action_failure_preserves_prior_scorable_and_negative_attempts():
    trainer, lifecycle = _new_trainer()
    lifecycle.before_error = RuntimeError("private callback error")
    lifecycle.before_error_index = 1

    with pytest.raises(RuntimeError, match="private callback error"):
        trainer._generate_and_score_completions(_inputs())

    records = trainer.observation_evidence[0]["records"]
    assert len(records) == 2
    assert records[0]["scorable"] is True
    assert records[0]["reward"] == 0.125
    assert records[1]["failure"] == "before_action_exception:RuntimeError"
    assert records[1]["scorable"] is False
    assert records[1]["reward"] is None
    assert sum(event[0] == "execute" for event in lifecycle.events) == 1
    assert lifecycle.finished[0][1] == "failed"


def test_mutating_pre_action_callback_receives_fresh_copy_each_time():
    trainer, lifecycle = _new_trainer()
    lifecycle.mutate_before_snapshot = True

    result = trainer._generate_and_score_completions(_inputs())

    assert result["rewards"] == [0.125, 0.125]
    assert lifecycle.before_snapshots[0] is not lifecycle.before_snapshots[1]
    assert lifecycle.state["alert"]["labels"]["alertname"] == "ObservedCpu"
    assert all(
        json.loads(row["prompt"])["alert"]["labels"]["alertname"] == "ObservedCpu"
        for row in trainer.generated_inputs
    )


def test_execute_exception_retains_failed_completion_evidence():
    trainer, lifecycle = _new_trainer()
    lifecycle.execute_error = RuntimeError("execution unavailable")

    with pytest.raises(RuntimeError, match="execution unavailable"):
        trainer._generate_and_score_completions(_inputs())

    record = trainer.observation_evidence[0]["records"][0]
    assert record["failure"] == "execute_exception:RuntimeError"
    assert record["completion_sha256"]
    assert record["action"]["tool"] == "kubectl_get"
    assert record["before_action_observed_at"] == "2026-10-01T00:00:01Z"
    assert record["result_classification"] == "NON_EMPIRICAL"


@pytest.mark.parametrize("method", ["begin", "before_action", "execute", "finish"])
def test_async_lifecycle_callbacks_are_rejected_and_finalized(method):
    trainer, lifecycle = _new_trainer()

    async def asynchronous(*_args, **_kwargs):
        return None

    setattr(lifecycle, method, asynchronous)
    with pytest.raises(TypeError, match="must be synchronous"):
        trainer._generate_and_score_completions(_inputs())

    assert trainer.observation_evidence[0]["status"] == "failed"
    if method == "execute":
        assert trainer.observation_evidence[0]["records"][0]["failure"] == (
            "execute_exception:TypeError"
        )
    if method == "finish":
        assert trainer.observation_evidence[0]["finish_callback_status"] == "raised"
        assert trainer.observation_evidence[0]["finish_error_type"] == "TypeError"


def test_generation_cancellation_runs_finish_and_marks_interrupted():
    import asyncio

    trainer, lifecycle = _new_trainer()
    trainer.generation_error = asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        trainer._generate_and_score_completions(_inputs())

    assert lifecycle.finished[0][1] == "interrupted"
    assert trainer.observation_evidence[0]["status"] == "interrupted"


def test_finish_failure_cannot_leave_a_completed_evidence_record():
    trainer, lifecycle = _new_trainer()
    lifecycle.finish_error = RuntimeError("cleanup failed")

    with pytest.raises(RuntimeError, match="cleanup failed"):
        trainer._generate_and_score_completions(_inputs())

    assert lifecycle.finished[0][1] == "completed"
    assert trainer.observation_evidence[0]["status"] == "failed"
    assert trainer.observation_evidence[0]["finish_callback_status"] == "raised"
    assert trainer.observation_evidence[0]["finish_error_type"] == "RuntimeError"
    assert trainer.observation_evidence[0]["result_classification"] == "NON_EMPIRICAL"
    assert trainer.observation_evidence[0]["certification_status"] == "NOT_CERTIFIED"


def test_group_tokens_are_distinct_and_evidence_property_is_defensive():
    trainer, lifecycle = _new_trainer()
    trainer._generate_and_score_completions(_inputs())
    first_token = trainer.generated_inputs[0]["g9_group_token"]
    trainer._generate_and_score_completions(_inputs())
    second_token = trainer.generated_inputs[0]["g9_group_token"]

    assert first_token != second_token
    assert len(trainer.observation_evidence) == 2
    record_copy = trainer.observation_evidence[0]
    record_copy["records"][0]["action"]["tool"] = "tampered"
    assert trainer.observation_evidence[0]["records"][0]["action"]["tool"] == (
        "kubectl_get"
    )
    assert "group_token" not in lifecycle.finished[0][0]


def test_previous_generation_group_token_cannot_bind_a_later_batch():
    trainer, lifecycle = _new_trainer()
    trainer._generate_and_score_completions(_inputs())
    prior_token = trainer.generated_inputs[0]["g9_group_token"]
    trainer.reward_input_mutator = lambda prompts, completions, kwargs: (
        prompts,
        completions,
        {**kwargs, "g9_group_token": [prior_token, prior_token]},
    )

    with pytest.raises(ValueError, match="g9_group_token"):
        trainer._generate_and_score_completions(_inputs())

    assert lifecycle.finished[1][1] == "failed"
    assert trainer.observation_evidence[1]["error_type"] == "ValueError"
    assert sum(event[0] == "execute" for event in lifecycle.events) == 2


def test_factory_rejects_other_trl_versions_before_importing_trl(monkeypatch):
    import builtins

    imported = []
    original_import = builtins.__import__
    monkeypatch.setattr(
        observation_first.importlib.metadata,
        "version",
        lambda package: "0.19.2" if package == "trl" else "1.0",
    )

    def guarded_import(name, *args, **kwargs):
        if name == "trl":
            imported.append(name)
            raise AssertionError("TRL import happened before version validation")
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    with pytest.raises(RuntimeError, match="requires TRL 0.19.1, found 0.19.2"):
        observation_first.make_trl_0191_observation_first_trainer()

    assert imported == []
