"""An unavailable external judge must not become a numeric grade."""

import asyncio
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


def test_ungraded_leaderboard_has_no_judge_mean(monkeypatch, capsys):
    import leaderboard

    monkeypatch.setattr(
        leaderboard,
        "run_episode",
        AsyncMock(return_value={"status": "error", "error": "judge_http_503"}),
    )
    summary = asyncio.run(
        leaderboard.eval_model(
            "fixture",
            {"display": "Fixture", "provider": "local", "type": "zero_shot"},
            [("single_fault/sf-001", "single_fault")],
        )
    )
    assert summary["avg_judge_score"] is None
    leaderboard.print_leaderboard([summary])
    assert "n/a" in capsys.readouterr().out
