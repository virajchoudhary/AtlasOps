"""An unavailable external judge must not become a numeric grade."""

import asyncio
import json
from unittest.mock import AsyncMock, Mock

import pytest


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        (Mock(status_code=503, text="unavailable"), "judge_http_503"),
        (
            Mock(
                status_code=200,
                json=Mock(
                    return_value={"choices": [{"message": {"content": "not JSON"}}]}
                ),
            ),
            "unparseable_response",
        ),
        (
            Mock(
                status_code=200,
                json=Mock(
                    return_value={
                        "choices": [
                            {
                                "message": {
                                    "content": (
                                        '{"correctness":0.8,"efficiency":0.7,'
                                        '"reasoning":0.9,"overall":1.2}'
                                    )
                                }
                            }
                        ]
                    }
                ),
            ),
            "invalid_grade",
        ),
    ],
)
def test_judge_outage_raises_instead_of_returning_a_grade(monkeypatch, response, reason):
    from agents import judge

    monkeypatch.setattr(judge, "post_with_retry", AsyncMock(return_value=response))
    with pytest.raises(judge.JudgeUnavailable, match=reason):
        asyncio.run(judge.judge_trajectory({}))


def test_valid_judge_grade_remains_measured(monkeypatch):
    from agents import judge

    response = Mock(
        status_code=200,
        json=Mock(
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": (
                                '{"correctness":0.8,"efficiency":0.7,'
                                '"reasoning":0.9,"overall":0.8}'
                            )
                        }
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(judge, "post_with_retry", AsyncMock(return_value=response))
    grade = asyncio.run(judge.judge_trajectory({}))
    assert grade["overall"] == 0.8
    assert grade["red_herring_handling"] == 0.5
    assert grade["judge_available"] is True


@pytest.mark.parametrize(
    "red_herring_handling",
    [-0.01, 1.01, True, "0.5", None, float("nan"), float("inf"), float("-inf")],
)
def test_invalid_red_herring_grade_is_unavailable(monkeypatch, red_herring_handling):
    from agents import judge

    response = Mock(
        status_code=200,
        json=Mock(
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "correctness": 0.8,
                                    "efficiency": 0.7,
                                    "reasoning": 0.9,
                                    "overall": 0.8,
                                    "red_herring_handling": red_herring_handling,
                                }
                            )
                        }
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(judge, "post_with_retry", AsyncMock(return_value=response))
    with pytest.raises(judge.JudgeUnavailable, match="invalid_grade"):
        asyncio.run(judge.judge_trajectory({}))


@pytest.mark.parametrize("red_herring_handling", [0, 0.25, 1])
def test_valid_red_herring_grade_is_measured(monkeypatch, red_herring_handling):
    from agents import judge

    response = Mock(
        status_code=200,
        json=Mock(
            return_value={
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {
                                    "correctness": 0.8,
                                    "efficiency": 0.7,
                                    "reasoning": 0.9,
                                    "overall": 0.8,
                                    "red_herring_handling": red_herring_handling,
                                }
                            )
                        }
                    }
                ]
            }
        ),
    )
    monkeypatch.setattr(judge, "post_with_retry", AsyncMock(return_value=response))
    grade = asyncio.run(judge.judge_trajectory({}))
    assert grade["red_herring_handling"] == red_herring_handling
    assert grade["judge_available"] is True


def test_ungraded_benchmark_has_no_judge_mean(tmp_path):
    from bench.runner import compute_summary, write_comparison_table

    summary = compute_summary(
        [{"scenario_id": "s1", "status": "error", "error": "judge_http_503"}],
        "ungraded",
        "fixture",
    )
    assert summary["avg_reward"] is None
    write_comparison_table(summary, tmp_path)
    assert "| n/a |" in (tmp_path / "comparison_table.md").read_text(encoding="utf-8")


def test_ungraded_legacy_evaluation_has_no_judge_mean():
    import eval as legacy_eval

    summary = legacy_eval.compute_stats(
        [{"scenario_id": "s1", "status": "error", "error": "judge_http_503"}],
        "ungraded",
    )
    assert summary["avg_judge_score"] is None


def test_ungraded_leaderboard_has_no_judge_mean(monkeypatch, capsys, tmp_path):
    import leaderboard

    monkeypatch.setattr(
        leaderboard,
        "run_episode",
        AsyncMock(return_value={"status": "error", "error": "judge_http_503"}),
    )
    monkeypatch.setattr(leaderboard, "RESULTS_DIR", tmp_path)
    summary = asyncio.run(
        leaderboard.eval_model(
            "fixture",
            {"display": "Fixture", "provider": "local", "type": "zero_shot"},
            [("single_fault/sf-001", "single_fault")],
        )
    )
    assert summary["avg_judge_score"] is None
    leaderboard.print_leaderboard([summary])
    output = capsys.readouterr().out
    assert "n/a" in output
    assert "Real GKE" not in output
    leaderboard.save_results([summary])
    table = (tmp_path / "leaderboard_table.md").read_text(encoding="utf-8")
    assert "| n/a |" in table
    assert "real GKE cluster" not in table
    assert "provenance must be verified separately" in table
