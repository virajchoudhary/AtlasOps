"""The right-sizing experiment report quotes these numbers; they must follow from the committed evidence."""

import json

from scripts import analyze_rightsizing_runs as analysis

RESULT = analysis.analyze()


def test_evidence_files_match_their_checksums():
    assert RESULT["checksum_mismatches"] == []


def test_each_grpo_run_started_from_the_policy_it_names():
    assert RESULT["start_policy_reproduced"] == {
        "grpo-direct-from-sft": True, "grpo-reasoned-from-sft16": True,
        "grpo-reasoned-graded-from-sft16": True, "grpo-reasoned-continued": True,
    }


def test_held_out_resolution_per_arm():
    resolved = {name: (arm["resolved"], arm["admissible"]) for name, arm in RESULT["arms"].items()}
    assert resolved == {
        "base_direct": (1, 64), "sft_direct_64": (14, 64), "grpo_direct": (10, 64),
        "base_reasoned": (3, 64), "sft_reasoned_64": (64, 64), "sft_reasoned_8": (0, 0),
        "sft_reasoned_16": (7, 34), "grpo_reasoned": (28, 64),
        "grpo_reasoned_graded": (42, 64), "grpo_reasoned_continued": (40, 64),
    }


def test_paired_changes_on_the_same_held_out_states():
    assert RESULT["paired"]["sft_direct_64->grpo_direct"] == {"lost": 4, "gained": 0, "exact_mcnemar_p": 0.125}
    assert RESULT["paired"]["sft_reasoned_16->grpo_reasoned"] == {"lost": 5, "gained": 26, "exact_mcnemar_p": 0.0002}


def test_reasoned_grpo_policy_divides_but_rounds_down():
    arm = RESULT["arms"]["grpo_reasoned"]
    assert arm["matches_rule"]["floor"] == 57 and arm["matches_rule"]["ceil"] == 28
    assert arm["exact_division_resolved"] == [24, 25] and arm["inexact_division_resolved"] == [4, 39]
    assert arm["miss_sizes"] == {-1: 33, 1: 3}
    assert arm["multi_line"] == 0


def test_grpo_group_reward_variation():
    groups = {run: data["groups"] for run, data in RESULT["grpo"].items()}
    assert groups["grpo-direct-from-sft"] == {"groups": 150, "varied": 20, "all_resolved": 27, "all_same_unresolved": 103}
    assert groups["grpo-reasoned-from-sft16"]["groups"] == 100 and groups["grpo-reasoned-from-sft16"]["varied"] == 38
    assert sum(block["resolved"] for block in RESULT["grpo"]["grpo-direct-from-base"]["blocks_of_10_steps"]) == 0


def test_every_run_is_labelled_outside_the_gates():
    for path in analysis.EVIDENCE.glob("*/RESULT.json"):
        record = json.loads(path.read_text())
        assert record["classification"] == "SMALL_MODEL_CPU_RL_EXPERIMENT"
        assert "Not a G" in record["gate_status"]
        assert record["source"]["dirty"] is False


def test_follow_ups_against_the_pre_registered_round_down_criterion():
    # Pre-registered: at least 20/39 inexact-division and 22/25 exact-division states solved.
    graded, continued = RESULT["arms"]["grpo_reasoned_graded"], RESULT["arms"]["grpo_reasoned_continued"]
    assert graded["inexact_division_resolved"] == [25, 39] and graded["exact_division_resolved"] == [17, 25]
    assert continued["inexact_division_resolved"] == [16, 39] and continued["exact_division_resolved"] == [24, 25]
    for arm in (graded, continued):
        met = arm["inexact_division_resolved"][0] >= 20 and arm["exact_division_resolved"][0] >= 22
        assert met is False
    assert graded["miss_sizes"] == {-1: 2, 1: 8, 2: 11, 3: 1}
    assert continued["miss_sizes"] == {-1: 23, 1: 1}
    assert RESULT["paired"]["grpo_reasoned->grpo_reasoned_graded"] == {
        "lost": 7, "gained": 21, "exact_mcnemar_p": 0.0125}
    assert RESULT["paired"]["grpo_reasoned->grpo_reasoned_continued"] == {
        "lost": 0, "gained": 12, "exact_mcnemar_p": 0.0005}
    assert RESULT["paired_inexact_division"]["grpo_reasoned->grpo_reasoned_graded"]["gained"] == 21
    assert RESULT["paired_inexact_division"]["grpo_reasoned->grpo_reasoned_continued"]["lost"] == 0


def test_v12_sets_are_fresh_disjoint_and_reproducible():
    from scripts import make_rightsizing_eval_sets as make
    from training import rightsizing_env as env

    path = analysis.EVIDENCE / "eval-sets-v1.2.json"
    committed = json.loads(path.read_text())
    assert committed == make.make_sets()
    sets = env.load_state_sets(path)
    dev, confirmation = set(sets["dev"]), set(sets["confirmation"])
    train, held_out = env.split_states(2026)
    touched = set().union(*make.touched_states().values())
    assert len(dev) == 64 and len(confirmation) == 128 and not dev & confirmation
    assert dev | confirmation <= set(train) and not (dev | confirmation) & (touched | set(held_out))


def test_v12_selects_a_checkpoint_on_dev_and_confirms_it_once():
    v12 = analysis.analyze_v12()
    assert v12["selected"] == "grpo-strict-balanced-ckpt-rerun-step-0030"
    assert v12["confirmed_adapter_is_selected_checkpoint"] is True
    dev = v12["dev"]["grpo-strict-balanced-ckpt-rerun-step-0030"]
    assert (dev["solved"], dev["exact"], dev["inexact"]) == (64, [20, 20], [44, 44])
    assert v12["dev_target_met"]["grpo-strict-from-sft48-final"] is False
    assert [v12["dev"][f"grpo-strict-balanced-ckpt-rerun-step-{step:04d}"]["solved"] for step in range(10, 101, 10)] \
        == [62, 63, 64, 59, 41, 21, 23, 30, 34, 32]
    confirmation = v12["confirmation"]
    assert confirmation["h1-step-0030"] == {"solved": 128, "n": 128, "exact": [37, 37], "inexact": [91, 91], "valid": 128}
    assert confirmation["s40-start"]["solved"] == 50
    assert confirmation["sft-reasoned-64"]["solved"] == 127
    assert confirmation["grpo-reasoned-continued"]["solved"] == 0
    assert confirmation["grpo-reasoned-continued-lenient"]["solved"] == 68
    assert v12["confirmation_paired_vs_start"]["gained"] == 78 and v12["confirmation_paired_vs_start"]["lost"] == 0
    assert v12["confirmation_met"] is True


def test_v12_scores_never_used_training_states():
    from training import rightsizing_env as env

    sets = env.load_state_sets(analysis.EVIDENCE / "eval-sets-v1.2.json")
    evaluation = set(sets["dev"]) | set(sets["confirmation"])
    for run in analysis.EVIDENCE.glob("grpo-strict-*/rollouts.jsonl"):
        prompts = {env.CapacityState(row["replicas"], row["demand"], row["capacity"])
                   for row in analysis.read_rows(run)}
        assert not prompts & evaluation, run.parent.name
    for name in ("sft-strict-32", "sft-strict-40", "sft-strict-48"):
        record = json.loads((analysis.EVIDENCE / name / "RESULT.json").read_text())
        assert record["data"]["excluded_states_overlap"] == 0


def test_v13_sets_are_fresh_disjoint_and_reproducible():
    from scripts import make_rightsizing_eval_sets as make
    from training import rightsizing_env as env

    path = analysis.REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1" / "eval-sets-v1.3.json"
    assert json.loads(path.read_text()) == make.make_v13_sets()
    sets = env.load_state_sets(path)
    dev, confirmation = set(sets["dev"]), set(sets["confirmation"])
    train, held_out = env.split_states(2026)
    touched = set().union(*make.v13_touched_states().values())
    assert len(dev) == 64 and len(confirmation) == 128 and not dev & confirmation
    assert dev | confirmation <= set(train)
    assert not (dev | confirmation) & (touched | set(held_out))


def test_v13_is_disjoint_from_every_v12_set_and_balanced_on_the_development_set():
    from training import rightsizing_env as env

    v13 = env.load_state_sets(analysis.REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1"
                              / "eval-sets-v1.3.json")
    v12 = env.load_state_sets(analysis.EVIDENCE / "eval-sets-v1.2.json")
    used_before = set(v12["dev"]) | set(v12["confirmation"])
    assert not (set(v13["dev"]) | set(v13["confirmation"])) & used_before
    exact = [state for state in v13["dev"] if state.demand % state.capacity_per_replica == 0]
    assert len(exact) == 32                                    # the development bar is read per category
    assert sum(1 for state in v13["confirmation"]
               if state.demand % state.capacity_per_replica == 0) == 42


GPU = analysis.analyze_gpu_v1()


def test_gpu_evidence_files_match_their_checksums():
    assert GPU["checksum_mismatches"] == []


def test_the_failed_first_gpu_attempt_is_still_in_the_record():
    """A run that died is kept and reported as failed, never quietly dropped."""
    attempt1 = {name: arm for name, arm in GPU["sft_arms"].items()
                if name.startswith("phaseA-attempt1/")}
    assert len(attempt1) == 3
    assert all(arm["failed"] for arm in attempt1.values())


def test_no_gpu_warm_start_ever_trained_on_an_evaluation_state():
    for name, arm in GPU["sft_arms"].items():
        if "excluded_states_overlap" in arm:
            assert arm["excluded_states_overlap"] == 0, name


def test_the_gpu_experiment_claims_no_gate():
    assert "NOT_PASSED/FROZEN" in GPU["gate_status"]
    assert "Exploratory" in GPU["gate_status"]


def test_every_well_formed_7b_answer_is_also_arithmetically_correct():
    """The warm starts teach format, not arithmetic: solved equals valid, and nothing misses."""
    for name, arm in GPU["sft_arms"].items():
        if arm.get("failed") or "sft" not in arm or arm["demonstrations"] < 20:
            continue
        sft = arm["sft"]
        assert sft["solved"] == sft["valid"], name


def test_exactly_one_attempt_chose_the_warm_start_and_the_rule_is_satisfied():
    assert GPU["sft_start"] == {"attempt": "phaseA4", "demonstrations": 18}
    sample = GPU["sft_selections"]["phaseA4"]["train_sample_by_size"]["18"]
    assert sample["admissible_rate"] >= 0.75
    assert 0.20 <= sample["resolved_rate"] <= 0.85


def test_the_grpo_run_changed_nothing_and_the_record_says_so():
    """Phase B logged 100 steps and moved no weight; three independent signs agree."""
    run = GPU["grpo"]["grpo-strict-balanced-7b-from-sft18"]
    assert run["trained_nothing"] is True
    assert run["null_run"] == {"lora_tensors_changed": 0, "adapter_identical_to_start": True,
                               "held_out_predictions_unchanged": True}
    assert run["lora"] == {"changed": 0, "tensors": 392}


def test_no_grpo_rollout_ever_earned_a_reward_above_the_floor():
    """Every group had identical reward, so the advantage -- and the gradient -- was zero."""
    run = GPU["grpo"]["grpo-strict-balanced-7b-from-sft18"]
    blocks = run["collapse_watch"]
    assert len(blocks) == 10
    assert {block["resolved"] for block in blocks} == {0}
    assert {block["invalid"] for block in blocks} == {160}
    assert sum(block["rollouts"] for block in blocks) == 1600


def test_the_rerun_trained_where_the_first_run_had_not():
    """phaseB2 is the same recipe with generation forced to eval mode; it moved every tensor."""
    run = GPU["grpo_rerun"]["grpo-strict-balanced-7b-from-sft18"]
    assert run["trained_nothing"] is False
    assert run["lora"] == {"changed": 392, "tensors": 392}
    assert run["paired_held_out"] == {"n": 64, "lost": 0, "gained": 9, "resolved_before": 55,
                                      "resolved_after": 64, "exact_mcnemar_p": 0.00390625}


def test_the_rerun_ran_out_of_signal_long_before_it_ran_out_of_steps():
    """Saturation, not collapse: a bounded reward gives zero advantage once every group resolves."""
    signal = GPU["grpo_rerun"]["grpo-strict-balanced-7b-from-sft18"]["learning_signal"]
    assert signal["steps_with_gradient"] == 13
    assert signal["last_step_with_gradient"] == 63
    assert signal["saturated"] is True


def test_both_phase_b_runs_stay_in_the_record():
    """The null run is not dropped once a working run exists."""
    assert GPU["grpo"]["grpo-strict-balanced-7b-from-sft18"]["trained_nothing"] is True
    assert set(GPU["grpo_rerun"]) == {"grpo-strict-balanced-7b-from-sft18"}


def test_the_development_sweep_selected_the_earliest_perfect_checkpoint():
    """Registered rule: most solved, earliest wins a tie."""
    assert GPU["selected"] == "step-0030"
    assert GPU["development_bar_met"] is True
    dev = GPU["dev"]
    assert (dev["sft-start"]["solved"], dev["step-0010"]["solved"],
            dev["step-0020"]["solved"], dev["step-0030"]["solved"]) == (49, 61, 59, 64)


def test_the_development_set_could_not_separate_the_candidates():
    """Nine of twelve are perfect, so the tie-break chose and the confirmation set decides."""
    perfect = [name for name, row in GPU["dev"].items() if row["solved"] == 64]
    assert len(perfect) == 9
    assert "step-0030" in perfect


def test_the_selection_never_read_a_confirmation_state():
    import json

    selection = json.loads((analysis.REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1"
                            / "phaseC1" / "SELECTION.json").read_text())
    assert selection["confirmation_set_opened_here"] is False
    assert selection["selected"] == "step-0030"


def test_both_failed_selection_attempts_are_still_in_the_record():
    base = analysis.REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1"
    for attempt in ("phaseC1-attempt1", "phaseC1-attempt2"):
        assert (base / attempt / "WHY_IT_FAILED.md").exists(), attempt
        assert (base / attempt / "console.log").exists(), attempt


def test_the_single_confirmation_met_the_pre_registered_criterion():
    """125/128 against the warm start's 109/128, paired on the same states."""
    assert GPU["confirmation"]["step-0030"]["solved"] == 125
    assert GPU["confirmation"]["sft-start"]["solved"] == 109
    assert GPU["confirmation_paired_vs_start"]["gained"] == 19
    assert GPU["confirmation_paired_vs_start"]["lost"] == 3
    assert GPU["confirmation_paired_vs_start"]["exact_mcnemar_p"] < 0.01
    assert GPU["criterion_met"] is True
    assert GPU["confirmation_adapter_is_the_selected_checkpoint"] is True


def test_the_confirmation_was_scored_only_on_confirmation_states():
    """The one set that decides must never have been trained on or selected on."""
    from training import rightsizing_env as env

    sets = env.load_state_sets(analysis.REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1"
                               / "eval-sets-v1.3.json")
    confirmation, dev = set(sets["confirmation"]), set(sets["dev"])
    base = analysis.REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1" / "phaseC2" / "confirmation"
    for arm in sorted(base.iterdir()):
        states = {env.CapacityState(row["replicas"], row["demand"], row["capacity"])
                  for row in analysis.read_rows(arm / "confirmation_rows.jsonl")}
        assert states <= confirmation, arm.name
        assert not states & dev, arm.name


def test_no_grpo_rollout_ever_touched_a_confirmation_or_development_state():
    from training import rightsizing_env as env

    sets = env.load_state_sets(analysis.REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1"
                               / "eval-sets-v1.3.json")
    evaluation = set(sets["dev"]) | set(sets["confirmation"])
    base = analysis.REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1"
    for rollouts in base.glob("phaseB*/*/rollouts.jsonl"):
        prompts = {env.CapacityState(row["replicas"], row["demand"], row["capacity"])
                   for row in analysis.read_rows(rollouts)}
        assert not prompts & evaluation, rollouts.parent.name


def test_the_selected_adapter_is_recorded_by_hash_and_not_committed():
    import json

    base = analysis.REPO / "artifacts" / "evidence" / "rl-gpu-7b-v1"
    record = json.loads((base / "phaseC2" / "SELECTED_ADAPTER.json").read_text())
    assert record["selected_checkpoint"] == "step-0030"
    assert len(record["archive_sha256"]) == 64
    checkpoints = json.loads((base / "phaseB2" / "grpo-strict-balanced-7b-from-sft18"
                              / "CHECKPOINT_SHA256.json").read_text())
    assert record["member_sha256"] == checkpoints["step-0030"]
    assert not list(base.rglob("*.safetensors")), "weights must never be committed"
