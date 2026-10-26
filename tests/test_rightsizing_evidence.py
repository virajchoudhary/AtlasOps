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
