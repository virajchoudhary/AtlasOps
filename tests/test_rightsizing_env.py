"""Model-free contracts for the right-sizing GRPO environment."""

import argparse
import json

import pytest

from training import rightsizing_env as env

STATE = env.CapacityState(replicas=2, demand=10, capacity_per_replica=3)  # needs 4


def action(replicas=4, **changes):
    payload = {"tool": "kubectl_scale",
               "arguments": {"deployment": "paymentservice", "namespace": "default", "replicas": replicas},
               "agent_claimed_resolved": False}
    payload.update(changes)
    return json.dumps(payload)


def test_states_are_saturated_unique_and_within_the_tool_policy_bound():
    states = env.all_states()
    assert len(states) == len(set(states))
    assert all(state.replicas < state.needed <= env.MAX_REPLICAS for state in states)
    assert {state.capacity_per_replica for state in states} == {1, 2, 3, 4, 5}


def test_split_is_disjoint_complete_and_deterministic():
    train, held_out = env.split_states()
    assert len(held_out) == 64
    assert not set(train) & set(held_out)
    assert set(train) | set(held_out) == set(env.all_states())
    assert env.states_sha256(held_out) == env.states_sha256(env.split_states()[1])
    assert env.states_sha256(held_out) != env.states_sha256(env.split_states(seed=7)[1])


def test_only_the_smallest_covering_replica_count_resolves():
    exact = env.score(STATE, action(4))
    assert exact["resolved"] and exact["reward"] == 1.0
    for replicas in (3, 5, 20):
        result = env.score(STATE, action(replicas))
        assert result["admissible"] and not result["resolved"]
        assert result["reward"] == pytest.approx(0.25 * 2 / 3, abs=1e-6)
    assert env.score(STATE, action(0))["reward"] == pytest.approx(0.25 / 3, abs=1e-6)


@pytest.mark.parametrize("completion, category", [
    (action(21), "policy_block"),
    (action("4"), "invalid_action"),
    ("```json\n" + action(4) + "\n```", "invalid_action"),
    ("The answer is 4.", "invalid_action"),
    (json.dumps({**json.loads(action(4)), "note": "x"}), "invalid_action"),
    (action(4, tool="kubectl_delete"), "invalid_action"),  # no schema, as in the G9 environment
    (json.dumps({"tool": "kubectl_scale", "arguments": {"deployment": "frontend", "replicas": 4},
                 "agent_claimed_resolved": False}), "target_mismatch"),
])
def test_invalid_or_blocked_actions_score_minus_one(completion, category):
    result = env.score(STATE, completion)
    assert result == {"reward": -1.0, "repository_reward": -1.0, "admissible": False, "resolved": False,
                      "category": category,
                      "replicas": None, "needed": 4}


def test_claims_never_change_the_reward():
    claimed = json.dumps({**json.loads(action(5)), "agent_claimed_resolved": True})
    assert env.score(STATE, claimed)["reward"] == env.score(STATE, action(5))["reward"]


def test_read_only_action_is_admissible_but_resolves_nothing():
    result = env.score(STATE, json.dumps({"tool": "kubectl_get", "arguments": {"resource": "deployments"},
                                          "agent_claimed_resolved": False}))
    assert result["admissible"] and not result["resolved"] and result["replicas"] == 2


def test_prompt_carries_the_observation_but_not_the_answer():
    messages = env.prompt_messages(STATE)
    observation = json.loads(messages[1]["content"])["observations"]
    assert observation["demand_units"] == 10 and observation["capacity_units_per_replica"] == 3
    assert '"needed"' not in messages[1]["content"]


def test_expert_demonstration_is_the_resolving_action():
    for state in env.split_states()[0][:50]:
        result = env.score(state, env.expert_completion(state))
        assert result["resolved"] and result["reward"] == 1.0


@pytest.mark.parametrize("command", ["train", "sft"])
def test_training_script_refuses_outputs_inside_the_repository(monkeypatch, command):
    import sys
    from pathlib import Path

    from scripts import train_rightsizing_grpo as script

    inside = Path(script.__file__).resolve().parents[1] / "runs-inside-repo"
    monkeypatch.setattr(sys, "argv", ["train_rightsizing_grpo", command, "--model-dir", "/nonexistent",
                                      "--output-dir", str(inside)])
    with pytest.raises(SystemExit):
        script.main()
    assert not inside.exists()


def test_reasoned_format_scores_only_the_final_action_line():
    expert = env.expert_completion(STATE, "reasoned")
    assert expert.splitlines()[0] == "10 / 3 = 3.33, rounded up = 4"
    assert env.score(STATE, env.final_action(expert, "reasoned"))["resolved"]
    wrong_reasoning = "10 / 3 = 99, rounded up = 99\n" + action(4)
    assert env.score(STATE, env.final_action(wrong_reasoning, "reasoned"))["reward"] == 1.0
    assert env.final_action("\n".join(["thinking"] * 5 + [action(4)]), "reasoned") == ""
    assert env.final_action(action(4) + "\nextra", "reasoned") == "extra"
    assert env.final_action("", "reasoned") == ""
    assert "Line 1 shows the arithmetic" in env.prompt_messages(STATE, "reasoned")[0]["content"]


def test_model_fetch_names_only_known_layers_and_needs_one_gguf():
    from scripts import fetch_qwen_oci_model as fetch

    manifest = {"config": {"digest": "sha256:c"},
                "layers": [{"mediaType": "application/vnd.docker.ai.gguf.v3", "digest": "sha256:g"},
                           {"mediaType": "application/vnd.docker.ai.license", "digest": "sha256:l"}]}
    assert fetch.blob_plan(manifest) == [("sha256:c", "config.json"), ("sha256:g", "model.gguf"),
                                         ("sha256:l", "LICENSE.txt")]
    for layers in ([{"mediaType": "application/vnd.docker.ai.unknown", "digest": "sha256:x"}],
                   [manifest["layers"][1]],
                   [manifest["layers"][0], manifest["layers"][0]]):
        with pytest.raises(ValueError):
            fetch.blob_plan({**manifest, "layers": layers})


def test_model_fetch_refuses_directories_inside_the_repository(monkeypatch, tmp_path):
    import sys
    from pathlib import Path

    from scripts import fetch_qwen_oci_model as fetch

    inside = Path(fetch.__file__).resolve().parents[1] / "model-inside-repo"
    monkeypatch.setattr(sys, "argv", ["fetch_qwen_oci_model", "--download-dir", str(inside),
                                      "--output-dir", str(tmp_path / "out")])
    with pytest.raises(SystemExit):
        fetch.main()
    assert not inside.exists()


def test_graded_reward_prefers_covering_demand_and_near_misses():
    state = env.CapacityState(replicas=1, demand=10, capacity_per_replica=3)  # needs 4
    graded = {replicas: env.score(state, action(replicas), "graded")["reward"] for replicas in (1, 3, 4, 5, 20)}
    assert graded[4] == 1.0
    assert graded[5] > graded[3] > graded[1]          # one too many beats one too few
    assert graded[3] > graded[20]                      # a far over-provision loses to a near miss
    assert env.score(state, action(5), "graded")["repository_reward"] == env.score(state, action(5))["reward"]
    assert env.score(state, "not json", "graded")["reward"] == -1.0
    claimed = json.dumps({**json.loads(action(5)), "agent_claimed_resolved": True})
    assert env.score(state, claimed, "graded")["reward"] == graded[5]
    with pytest.raises(ValueError):
        env.score(state, action(4), "shaped")


def test_repository_reward_stays_the_default():
    assert env.score(STATE, action(5))["reward"] == pytest.approx(0.25 * 2 / 3, abs=1e-6)
    assert env.score(STATE, action(3))["reward"] == env.score(STATE, action(5))["reward"]


def test_strict_format_scores_only_the_two_instructed_lines():
    expert = env.expert_completion(STATE, "reasoned_strict")
    assert expert == env.expert_completion(STATE, "reasoned")
    assert env.score(STATE, env.final_action(expert, "reasoned_strict"))["resolved"]
    bare = action(4)
    assert env.final_action(bare, "reasoned_strict") == ""                       # arithmetic dropped
    assert env.final_action("10 / 3 = 3.33, rounded up = 4\nok\n" + bare, "reasoned_strict") == ""
    assert env.final_action("10 divided by 3 is about 3\n" + bare, "reasoned_strict") == ""
    wrong_numbers = "10 / 3 = 9.00, rounded up = 9\n" + bare
    assert env.score(STATE, env.final_action(wrong_numbers, "reasoned_strict"))["resolved"]  # action is scored
    assert env.prompt_messages(STATE, "reasoned_strict") == env.prompt_messages(STATE, "reasoned")


def test_state_sets_refuse_a_changed_set(tmp_path):
    path = tmp_path / "sets.json"
    states = [env.CapacityState(2, 10, 3)]
    payload = {"sets": {"dev": [{"replicas": 2, "demand": 10, "capacity_per_replica": 3}]},
               "sha256": {"dev": env.states_sha256(states)}}
    path.write_text(json.dumps(payload))
    assert env.load_state_sets(path) == {"dev": states}
    payload["sets"]["dev"][0]["demand"] = 11
    path.write_text(json.dumps(payload))
    with pytest.raises(ValueError):
        env.load_state_sets(path)


def _stub_loader(monkeypatch, *, cuda_available):
    """Stand-ins for torch, transformers and peft, so loading can be checked without them."""
    import sys
    import types

    calls: dict[str, object] = {}

    torch = types.ModuleType("torch")
    torch.float32 = "float32"
    torch.float16 = "float16"
    torch.cuda = types.SimpleNamespace(is_available=lambda: cuda_available)

    transformers = types.ModuleType("transformers")

    def from_pretrained(model_dir, **kwargs):
        calls["model"] = kwargs
        return "model"

    transformers.AutoModelForCausalLM = types.SimpleNamespace(from_pretrained=from_pretrained)
    transformers.AutoTokenizer = types.SimpleNamespace(from_pretrained=lambda *a, **k: "tokenizer")
    transformers.BitsAndBytesConfig = lambda **kwargs: ("bnb", kwargs)

    peft = types.ModuleType("peft")

    def prepare(model, **kwargs):
        calls["prepared"] = kwargs
        return "prepared"

    peft.prepare_model_for_kbit_training = prepare

    for name, module in [("torch", torch), ("transformers", transformers), ("peft", peft)]:
        monkeypatch.setitem(sys.modules, name, module)
    return calls


def test_the_cpu_loading_path_is_float32_and_unquantised(monkeypatch):
    from scripts import train_rightsizing_grpo as script

    calls = _stub_loader(monkeypatch, cuda_available=False)
    assert script.load_base("/model") == ("model", "tokenizer")          # cpu is the default
    assert calls["model"] == {"local_files_only": True, "dtype": "float32"}
    assert "prepared" not in calls


def test_cuda_loads_the_same_checkpoint_in_four_bit_nf4_with_fp16_compute(monkeypatch):
    from scripts import train_rightsizing_grpo as script

    calls = _stub_loader(monkeypatch, cuda_available=True)
    assert script.load_base("/model", "cuda") == ("prepared", "tokenizer")
    assert calls["model"]["device_map"] == {"": 0}
    assert calls["model"]["quantization_config"] == ("bnb", {
        "load_in_4bit": True, "bnb_4bit_quant_type": "nf4",
        "bnb_4bit_compute_dtype": "float16", "bnb_4bit_use_double_quant": True})
    assert calls["prepared"] == {"use_gradient_checkpointing": False}
    assert script.load_base("/model", "cuda", gradient_checkpointing=True) == ("prepared", "tokenizer")
    assert calls["prepared"] == {"use_gradient_checkpointing": True}


def test_cuda_is_refused_when_no_gpu_is_present(monkeypatch):
    from scripts import train_rightsizing_grpo as script

    _stub_loader(monkeypatch, cuda_available=False)
    with pytest.raises(SystemExit):
        script.load_base("/model", "cuda")


def test_an_unknown_device_is_refused():
    from scripts import train_rightsizing_grpo as script

    with pytest.raises(SystemExit):
        script.load_base("/model", "mps")


@pytest.mark.parametrize("command,argv", [
    ("train", ["--model-dir", "/m", "--output-dir", "/o"]),
    ("sft", ["--model-dir", "/m", "--output-dir", "/o"]),
    ("score", ["--model-dir", "/m", "--states", "/s", "--set", "dev",
               "--action-format", "reasoned_strict", "--output-dir", "/o"]),
    ("evaluate", ["--model-dir", "/m", "--run-dir", "/r"]),
])
def test_every_command_still_defaults_to_the_cpu(command, argv):
    from scripts import train_rightsizing_grpo as script

    parsed = script.build_parser().parse_args([command, *argv])
    assert parsed.device == "cpu" and parsed.gradient_checkpointing is False
    assert script.build_parser().parse_args([command, *argv, "--device", "cuda"]).device == "cuda"


def test_an_exported_archive_records_its_commit_and_says_it_has_no_working_tree(monkeypatch, tmp_path):
    """A GPU host gets the code as an export; the record must not imply a clean checkout."""
    from scripts import train_rightsizing_grpo as script

    (tmp_path / "training").mkdir()
    (tmp_path / "scripts").mkdir()
    (tmp_path / "training" / "a.py").write_text("x = 1\n")
    monkeypatch.setattr(script, "REPO", tmp_path)

    monkeypatch.delenv("ATLASOPS_SOURCE_COMMIT", raising=False)
    with pytest.raises(SystemExit):
        script.source_identity()                       # an unknown source is never recorded

    monkeypatch.setenv("ATLASOPS_SOURCE_COMMIT", "b643cf0")
    identity = script.source_identity()
    assert identity["commit"] == "b643cf0"
    assert identity["dirty"] is None and identity["from"] == "exported archive, no working tree"

    digest = identity["tree_sha256"]
    (tmp_path / "training" / "a.py").write_text("x = 2\n")
    assert script.source_identity()["tree_sha256"] != digest     # the digest tracks the code


class _ModeSpy:
    """The part of a torch module the eval-mode wrapper touches, without importing torch."""

    def __init__(self, explode=False):
        self.training = False
        self.explode = explode
        self.seen = []

    def train(self):
        self.training = True

    def eval(self):
        self.training = False

    def generate(self, *args, **kwargs):
        self.seen.append(self.training)
        if self.explode:
            raise RuntimeError("out of memory")
        return "completion"


def test_generation_runs_in_eval_mode_and_restores_training_mode():
    """The wrapper that made GRPO's rollouts usable: eval during generate, train after."""
    from scripts import train_rightsizing_grpo as script

    model = _ModeSpy()
    script.generate_in_eval_mode(model)

    model.train()
    assert model.generate() == "completion"
    assert model.seen == [False]          # generated off the training path
    assert model.training is True         # and the trainer's own mode handed back

    model.eval()
    model.generate()
    assert model.seen == [False, False] and model.training is False


def test_a_failing_generate_still_restores_training_mode():
    from scripts import train_rightsizing_grpo as script

    model = _ModeSpy(explode=True)
    script.generate_in_eval_mode(model)
    model.train()
    with pytest.raises(RuntimeError):
        model.generate()
    assert model.training is True


def test_every_subcommand_supplies_the_arguments_its_handler_reads():
    """A handler reads args.gradient_checkpointing; each subparser must define it.

    Phase C1 lost a GPU sweep to a hand-built Namespace that lacked this attribute. The
    CLI was never broken, but nothing pinned that, so the guarantee is written down here.
    """
    from scripts import train_rightsizing_grpo as script

    parser = script.build_parser()
    for command, required in (("train", ["model_dir", "gradient_checkpointing"]),
                              ("sft", ["model_dir", "gradient_checkpointing"]),
                              ("score", ["model_dir", "gradient_checkpointing"]),
                              ("evaluate", ["model_dir", "gradient_checkpointing"])):
        sub = next(action for action in parser._actions
                   if isinstance(action, argparse._SubParsersAction)).choices[command]
        defined = {action.dest for action in sub._actions}
        assert set(required) <= defined, f"{command} is missing {set(required) - defined}"
