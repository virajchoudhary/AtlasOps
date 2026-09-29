"""Loss-preserving structural normalization of hash-checked G9 event streams."""

from __future__ import annotations

import hashlib
import json

import pytest

from bench.episode_membership import normalize_g9_event_observations


def _jsonl(events: list[dict]) -> bytes:
    return "".join(
        json.dumps(event, separators=(",", ":"), ensure_ascii=False) + "\n"
        for event in events
    ).encode("utf-8")


def _expected_sha256(raw_bytes: bytes) -> str:
    return hashlib.sha256(raw_bytes).hexdigest()


def _started(scenario_id: str = "single_fault/sf-006") -> dict:
    return {
        "recorded_at": "2026-09-29T12:00:00Z",
        "event": "run_started",
        "evaluation_mode": "EMPIRICAL",
        "split": "val",
        "split_sha256": "a" * 64,
        "provenance": {"base_model": {"id": "example-model"}},
        "evaluator_source": {"git_sha": "b" * 40, "git_dirty": False},
        "unknown_header_field": {"retained": True},
    }


def _episode(scenario_id: str = "single_fault/sf-006") -> list[dict]:
    return [
        {
            "recorded_at": "2026-09-29T12:00:01Z",
            "event": "episode_started",
            "evaluation_mode": "EMPIRICAL",
            "scenario_id": scenario_id,
            "seed": 7,
            "unknown_start_field": "retained",
        },
        {
            "recorded_at": "2026-09-29T12:00:02Z",
            "event": "policy_output",
            "scenario_id": scenario_id,
            "step": 0,
            "raw_policy_output": "synthetic fixture",
            "unknown_progress_field": {"retained": ["as", "is"]},
        },
        {
            "recorded_at": "2026-09-29T12:00:03Z",
            "event": "episode_completed",
            "scenario_id": scenario_id,
            "result": {
                "scenario_id": scenario_id,
                "status": "ok",
                "scorable": True,
                "reward": 0.5,
                "unknown_result_field": "retained",
            },
        },
    ]


def _run_completed(*, empirical_claim_allowed: bool = True) -> dict:
    return {
        "recorded_at": "2026-09-29T12:00:04Z",
        "event": "run_completed",
        "summary": {
            "evaluation_mode": "empirical",
            "empirical_claim_allowed": empirical_claim_allowed,
            "unknown_summary_field": {"retained": True},
        },
    }


def test_normalizes_hash_checked_completed_stream_without_claiming_metrics():
    events = [_started(), *_episode(), _run_completed()]
    raw_bytes = _jsonl(events)

    normalized = normalize_g9_event_observations(
        raw_bytes,
        expected_sha256=_expected_sha256(raw_bytes),
    )

    assert normalized["source_sha256"] == _expected_sha256(raw_bytes)
    assert normalized["run_outcome"] == "completed"
    assert normalized["non_empirical"] is True
    assert normalized["empirical_claim_allowed"] is False
    assert normalized["certification_status"] == "NOT_CERTIFIED"
    assert normalized["raw_events"] == events
    assert normalized["episodes"] == [
        {
            "scenario_id": "single_fault/sf-006",
            "started": True,
            "outcome": "completed",
            "events": events[1:4],
            "terminal_event": events[3],
            "result": events[3]["result"],
        }
    ]
    assert not {
        "resolution_rate",
        "reward",
        "time_to_resolve_s",
        "denominator",
    }.intersection(normalized)


@pytest.mark.parametrize(
    ("terminal_name", "status", "scorable", "expected_outcome"),
    [
        ("episode_failed", "failed", False, "failed"),
        ("episode_unscorable", "unscorable", False, "unscorable"),
    ],
)
def test_preserves_failed_and_unscorable_g9_results(
    terminal_name, status, scorable, expected_outcome
):
    scenario_id = "single_fault/sf-006"
    terminal = {
        "event": terminal_name,
        "result": {
            "scenario_id": scenario_id,
            "status": status,
            "scorable": scorable,
            "failure": "synthetic",
            "unknown_result_field": {"retained": True},
        },
    }
    events = [_started(), {"event": "episode_started", "scenario_id": scenario_id}, terminal]
    raw_bytes = _jsonl(events)

    normalized = normalize_g9_event_observations(
        raw_bytes,
        expected_sha256=_expected_sha256(raw_bytes),
    )

    assert normalized["run_outcome"] == "partial"
    assert normalized["episodes"][0] == {
        "scenario_id": scenario_id,
        "started": True,
        "outcome": expected_outcome,
        "events": events[1:],
        "terminal_event": terminal,
        "result": terminal["result"],
    }
    assert normalized["raw_events"] == events


def test_preserves_episode_observations_and_partial_active_episode_on_run_interruption():
    completed_id = "single_fault/sf-006"
    active_id = "single_fault/sf-007"
    completed = _episode(completed_id)
    completed[-1]["result"].update({"scenario_id": completed_id})
    events = [
        _started(),
        *completed,
        {
            "event": "episode_started",
            "scenario_id": active_id,
            "evaluation_mode": "EMPIRICAL",
            "started_at": "2026-09-29T12:00:05Z",
        },
        {
            "event": "policy_output",
            "scenario_id": active_id,
            "raw_policy_output": "partial",
            "step": 0,
        },
        {
            "event": "run_interrupted",
            "scenario_id": active_id,
            "completed_episodes": 1,
            "unknown_interrupt_field": "retained",
        },
    ]
    raw_bytes = _jsonl(events)

    normalized = normalize_g9_event_observations(
        raw_bytes,
        expected_sha256=_expected_sha256(raw_bytes),
    )

    assert normalized["run_outcome"] == "interrupted"
    assert [episode["outcome"] for episode in normalized["episodes"]] == [
        "completed",
        "interrupted",
    ]
    assert normalized["episodes"][1]["scenario_id"] == active_id
    assert normalized["episodes"][1]["started"] is True
    assert normalized["episodes"][1]["events"] == events[4:7]
    assert "result" not in normalized["episodes"][1]
    assert normalized["raw_events"] == events


def test_open_episode_at_eof_remains_partial_and_nonclaimable():
    scenario_id = "single_fault/sf-006"
    events = [
        _started(),
        {"event": "episode_started", "scenario_id": scenario_id},
        {"event": "policy_output", "scenario_id": scenario_id, "raw_policy_output": "partial"},
    ]
    raw_bytes = _jsonl(events)

    normalized = normalize_g9_event_observations(
        raw_bytes,
        expected_sha256=_expected_sha256(raw_bytes),
    )

    assert normalized["run_outcome"] == "partial"
    assert normalized["empirical_claim_allowed"] is False
    assert normalized["episodes"] == [
        {
            "scenario_id": scenario_id,
            "started": True,
            "outcome": "partial",
            "events": events[1:],
        }
    ]


def test_preserves_unstarted_scenario_when_state_provider_is_interrupted():
    scenario_id = "single_fault/sf-006"
    interrupted = {
        "event": "run_interrupted",
        "scenario_id": scenario_id,
        "completed_episodes": 0,
    }
    events = [_started(), interrupted]
    raw_bytes = _jsonl(events)

    normalized = normalize_g9_event_observations(
        raw_bytes,
        expected_sha256=_expected_sha256(raw_bytes),
    )

    assert normalized["run_outcome"] == "interrupted"
    assert normalized["episodes"] == [
        {
            "scenario_id": scenario_id,
            "started": False,
            "outcome": "interrupted",
            "events": [interrupted],
        }
    ]


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        ("duplicate_start", "duplicate"),
        ("cross_scenario_progress", "cross-scenario"),
        ("mismatched_result", "result scenario_id"),
        ("terminal_without_start", "without an active episode"),
        ("progress_after_terminal", "without an active episode"),
        ("post_run_terminal", "final"),
        ("duplicate_scenario", "duplicate scenario"),
    ],
)
def test_rejects_invalid_lifecycle_order_and_scenario_mismatch(mutate, message):
    first_id = "single_fault/sf-006"
    second_id = "single_fault/sf-007"
    episode_events = _episode(first_id)
    if mutate == "duplicate_start":
        episode_events.insert(1, dict(episode_events[0]))
    elif mutate == "cross_scenario_progress":
        episode_events[1]["scenario_id"] = second_id
    elif mutate == "mismatched_result":
        episode_events[-1]["result"]["scenario_id"] = second_id
    elif mutate == "terminal_without_start":
        episode_events = [episode_events[-1]]
    elif mutate == "progress_after_terminal":
        episode_events.append({"event": "policy_output", "scenario_id": first_id})
    elif mutate == "post_run_terminal":
        episode_events.append(_run_completed())
        episode_events.append({"event": "episode_started", "scenario_id": second_id})
    else:
        episode_events.extend(_episode(first_id))
    events = [_started(), *episode_events]
    raw_bytes = _jsonl(events)

    with pytest.raises(ValueError, match=message):
        normalize_g9_event_observations(
            raw_bytes,
            expected_sha256=_expected_sha256(raw_bytes),
        )


def test_rejects_run_started_when_it_is_not_first():
    events = [*_episode(), _started()]
    raw_bytes = _jsonl(events)

    with pytest.raises(ValueError, match="begin with exactly one run_started"):
        normalize_g9_event_observations(
            raw_bytes,
            expected_sha256=_expected_sha256(raw_bytes),
        )


def test_accepts_claim_ineligible_empirical_run_without_promoting_it():
    events = [_started(), *_episode(), _run_completed(empirical_claim_allowed=False)]
    raw_bytes = _jsonl(events)

    normalized = normalize_g9_event_observations(
        raw_bytes,
        expected_sha256=_expected_sha256(raw_bytes),
    )

    assert normalized["run_outcome"] == "completed"
    assert normalized["empirical_claim_allowed"] is False
    assert normalized["raw_events"][-1]["summary"]["empirical_claim_allowed"] is False


@pytest.mark.parametrize(
    ("raw_bytes", "message"),
    [
        (b'{"event":"run_started","number":NaN}\n', "non-finite"),
        (b'{"event":"run_started",}\n', "malformed JSONL"),
        (b'{"event":"run_started","event":"run_started"}\n', "duplicate field"),
        (b'{"event":"run_started","evaluation_mode":"NON_EMPIRICAL"}\n', "non-empirical marker"),
        (b'{"event":"run_started","non_empirical":true}\n', "non-empirical marker"),
        (
            b'{"event":"run_started","test_only_synthetic_fixture":true}\n',
            "non-empirical marker",
        ),
        (b'{"event":"run_started","mock_eval":true}\n', "non-empirical marker"),
    ],
)
def test_rejects_malformed_non_finite_duplicate_and_mock_marked_sources(
    raw_bytes, message
):
    with pytest.raises(ValueError, match=message):
        normalize_g9_event_observations(
            raw_bytes,
            expected_sha256=_expected_sha256(raw_bytes),
        )


def test_requires_matching_sha256():
    raw_bytes = _jsonl([_started()])

    with pytest.raises(ValueError, match="SHA-256 mismatch"):
        normalize_g9_event_observations(raw_bytes, expected_sha256="0" * 64)

    with pytest.raises(ValueError, match="64 hexadecimal"):
        normalize_g9_event_observations(raw_bytes, expected_sha256="not-a-digest")


@pytest.mark.parametrize(
    ("limit_name", "limit"),
    [
        ("MAX_RAW_OUTPUT_BYTES", 16),
        ("MAX_RAW_OUTPUT_LINE_BYTES", 16),
        ("MAX_RAW_OUTPUT_RECORDS", 1),
    ],
)
def test_observation_parser_respects_raw_jsonl_bounds(monkeypatch, limit_name, limit):
    monkeypatch.setattr("bench.episode_membership." + limit_name, limit)
    events = [_started(), *_episode()]
    raw_bytes = _jsonl(events)

    with pytest.raises(ValueError, match="exceeds"):
        normalize_g9_event_observations(
            raw_bytes,
            expected_sha256=_expected_sha256(raw_bytes),
        )
