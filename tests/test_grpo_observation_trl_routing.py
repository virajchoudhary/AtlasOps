"""Model-free contract checks for the two pinned TRL routing methods.

Generation, tensors and the environment are test boundaries. This does not
exercise a real model, optimizer, tokenizer or live incident.
"""

from __future__ import annotations

import ast
import json
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from training.grpo_observation_first import ObservationFirstGRPOMixin

from tests.test_grpo_observation_first import COMPLETION, _Lifecycle, _inputs


def _routing_class():
    path = Path(__file__).parent / "fixtures" / "trl_0191_observation_routing.txt"
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef):
            node.decorator_list = []
            node.returns = None
        elif isinstance(node, ast.arg):
            node.annotation = None
    namespace = {
        "torch": SimpleNamespace(
            zeros=lambda rows, columns, **kw: np.zeros((rows, columns)),
            tensor=lambda values, **kw: np.array(values),
            float32=np.float32,
            nan=np.nan,
            isnan=lambda values: SimpleNamespace(
                all=lambda dim: np.isnan(values).all(axis=dim)
            ),
        ),
        "nn": SimpleNamespace(Module=type("Module", (), {})),
        "profiling_context": lambda *args: nullcontext(),
        "gather": lambda values: values,
        "shuffle_tensor_dict": lambda values: values,
        "split_tensor_dict": lambda values, count: [values] * count,
    }
    exec(compile(tree, str(path), "exec"), namespace)
    return namespace["GRPOTrainer"]


class _GenerationBoundary(_routing_class()):
    def __init__(self, *, reward_funcs, args):
        self.args = args
        self.reward_funcs = reward_funcs
        self.num_generations = args.num_generations
        self.max_prompt_length = args.max_prompt_length
        self.num_iterations = 2
        self._step = 0
        self._buffered_inputs = None
        self.model = SimpleNamespace(training=True)
        self.accelerator = SimpleNamespace(device="cpu")
        self.reward_processing_classes = [None]
        self.reward_func_names = ["observation"]
        self.generation_inputs = []
        self.reorder_binding = False

    def _generate_and_score_completions(self, inputs):
        self.generation_inputs.append(inputs)
        prompts = [row["prompt"] for row in inputs]
        if self.reorder_binding:
            inputs = list(reversed(inputs))
        rewards = self._calculate_rewards(
            inputs, prompts, [COMPLETION] * len(inputs), [[42]] * len(inputs)
        )
        return {"rewards": rewards.tolist()}


class _Trainer(ObservationFirstGRPOMixin, _GenerationBoundary):
    pass


def _trainer():
    lifecycle = _Lifecycle()
    trainer = _Trainer(
        args=SimpleNamespace(
            max_prompt_length=None, num_generations=2, steps_per_generation=2
        ),
        observation_lifecycle=lifecycle,
    )
    return trainer, lifecycle


def test_pinned_prepare_inputs_generates_from_observed_state_and_routes_bound_rewards():
    trainer, lifecycle = _trainer()

    result = trainer._prepare_inputs(_inputs())

    assert result["rewards"] == [[0.125], [0.125]]
    assert lifecycle.events[0] == ("begin", "single_fault/sf-002")
    assert [
        event[2] for event in lifecycle.events if event[0] == "execute"
    ] == [COMPLETION, COMPLETION]
    generated = trainer.generation_inputs[0]
    assert [
        json.loads(row["prompt"])["alert"]["labels"]["alertname"]
        for row in generated
    ] == ["ObservedCpu", "ObservedCpu"]
    assert [row["g9_sample_index"] for row in generated] == [0, 1]
    assert trainer.observation_evidence[0]["status"] == "completed"


def test_pinned_prepare_inputs_reuses_buffer_without_reexecuting_environment():
    trainer, lifecycle = _trainer()
    first = trainer._prepare_inputs(_inputs())
    first_events = list(lifecycle.events)

    for _ in range(3):
        assert trainer._prepare_inputs(_inputs()) == first
        assert lifecycle.events == first_events

    trainer._prepare_inputs(_inputs())
    assert len(trainer.generation_inputs) == 2
    evidence = trainer.observation_evidence
    assert len(evidence) == 2
    assert trainer.generation_inputs[0][0]["g9_group_token"] != (
        trainer.generation_inputs[1][0]["g9_group_token"]
    )


def test_pinned_empty_buffer_reacquires_observation_instead_of_reusing_group():
    trainer, _ = _trainer()
    trainer._prepare_inputs(_inputs())
    trainer._buffered_inputs = None

    trainer._prepare_inputs(_inputs())

    assert len(trainer.generation_inputs) == 2
    assert len(trainer.observation_evidence) == 2
    assert trainer.generation_inputs[0][0]["g9_group_token"] != (
        trainer.generation_inputs[1][0]["g9_group_token"]
    )


def test_pinned_eval_routing_refuses_training_lifecycle_before_observation():
    trainer, lifecycle = _trainer()
    trainer.model.training = False

    with pytest.raises(RuntimeError, match="training mode"):
        trainer._prepare_inputs(_inputs())

    assert lifecycle.events == []
    assert trainer.observation_evidence == ()
    assert trainer.generation_inputs == []
    assert trainer._step == 0


@pytest.mark.parametrize("mode", [None, 0, 1, "true"])
def test_unknown_or_non_boolean_training_mode_cannot_start_lifecycle(mode):
    trainer, lifecycle = _trainer()
    trainer.model.training = mode

    with pytest.raises(RuntimeError, match="training mode"):
        trainer._generate_and_score_completions(_inputs())

    assert lifecycle.events == []
    assert trainer.observation_evidence == ()
    assert trainer.generation_inputs == []


def test_pinned_reward_column_routing_detects_reordered_binding_before_action():
    trainer, lifecycle = _trainer()
    trainer.reorder_binding = True

    with pytest.raises(ValueError, match="reordered"):
        trainer._prepare_inputs(_inputs())

    assert not any(event[0] == "execute" for event in lifecycle.events)
    assert trainer.observation_evidence[0]["status"] == "failed"
