from __future__ import annotations

import json
from collections import defaultdict

import pytest

from agents.tool_policy import ROLE_ALLOWED_TOOLS
from config.scenario_catalog import TRAIN_SPLIT
from training import build_sft_candidate as builder
from training.sft_candidate import OUTCOMES, inspect_candidate_rows
from training.sft_rendering import SFT_ROLES, validate_message_sequence


def _content(message: dict) -> dict:
    return json.loads(message["content"])


def _calls(row: dict) -> list[tuple[int, dict]]:
    return [
        (index, call)
        for index, message in enumerate(row["messages"])
        for call in message.get("tool_calls") or []
    ]


def test_builder_is_deterministic_train_only_and_admission_ready():
    rows = builder.build_candidate_rows()
    assert rows == builder.build_candidate_rows()

    inventory = inspect_candidate_rows(rows)
    assert len(rows) == 68
    assert inventory["total_scenarios"] == len(TRAIN_SPLIT) == 16
    assert set(inventory["scenario_ids"]) == set(TRAIN_SPLIT)
    assert inventory["role_distribution"] == {role: 17 for role in SFT_ROLES}
    assert set(inventory["outcome_distribution"]) == OUTCOMES
    assert inventory["exact_duplicate_assistant_targets"] == 0
    assert inventory["technical_admissibility"] == "PASS"
    assert inventory["d3_approval"] == "PENDING"

    case_rows: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        case_rows[(row["scenario_id"], row["case_id"])].append(row)
    assert len(case_rows) == builder.CONSTRUCTION_CONFIG["case_groups"] == 17
    for grouped in case_rows.values():
        assert {row["role"] for row in grouped} == set(SFT_ROLES)
        assert all(row["safety"] == grouped[0]["safety"] for row in grouped)

    diagnosis_rows = [row for row in rows if row["role"] == "diagnosis"]
    for row in diagnosis_rows:
        context = _content(row["messages"][1])
        severity = context["triage"]["severity"]
        metrics_reply = next(
            message for message in row["messages"]
            if message["role"] == "tool" and message["tool_name"] == "promql_query"
        )
        output = _content(metrics_reply)
        series = output["series"]
        impact = context["triage"]["blast_radius"]["user_impact_pct"]
        if severity == "P0":
            assert impact == 100.0
            assert all(item["error_ratio"] > 0.9 for item in series)
        elif severity == "P1":
            assert impact > 5
            assert all(item["error_ratio"] > 0.05 for item in series)
        else:
            assert impact == 0.0
            assert output["evidence_status"] in {
                "observed",
                "no_series",
                "recheck_after_malformed_input",
            }
            assert all(item["error_ratio"] <= 0.05 for item in series)
    no_series = next(
        row for row in diagnosis_rows
        if _content(row["messages"][1])["alert"]["alerts"][0]["annotations"]["description"]
        .startswith("The synthetic query returned no error series")
    )
    assert "must not be interpreted as zero errors" in (
        _content(no_series["messages"][1])["alert"]["alerts"][0]["annotations"]["description"]
    )


def test_wire_calls_are_role_allowed_and_immediately_paired():
    rows = builder.build_candidate_rows()
    inspect_candidate_rows(rows)

    for row in rows:
        messages = row["messages"]
        validate_message_sequence(messages)
        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == builder.CANONICAL_RUNTIME_PROMPT_PLACEHOLDER
        assert messages[1]["role"] == "user"
        assert _content(messages[1])["simulation"] is True

        for index, message in enumerate(messages):
            calls = message.get("tool_calls") or []
            assert len(calls) <= 1
            if calls:
                call = calls[0]
                assert message["role"] == "assistant"
                assert call["type"] == "function"
                function = call["function"]
                assert function["name"] in ROLE_ALLOWED_TOOLS[row["role"]]
                assert isinstance(json.loads(function["arguments"]), dict)
                reply = messages[index + 1]
                assert reply["role"] == "tool"
                assert reply["tool_call_id"] == call["id"]
                assert reply["tool_name"] == function["name"]
                assert _content(reply)["simulation"] is True
            if message["role"] == "tool":
                assert index > 0
                previous_calls = messages[index - 1].get("tool_calls") or []
                assert len(previous_calls) == 1
                assert previous_calls[0]["id"] == message["tool_call_id"]

        serialized = json.dumps(messages, sort_keys=True)
        for forbidden in (
            '"scenario_id"',
            '"case_id"',
            '"expected_alert"',
            '"expected_root_cause"',
            '"expected_remediation"',
            '"judge_score"',
            '"reward_contract"',
        ):
            assert forbidden not in serialized


def test_approval_blocking_verifier_binding_and_comms_gate():
    rows = builder.build_candidate_rows()
    decisions = {
        (row["safety"]["severity"], row["safety"]["approval"]["status"])
        for row in rows
    }
    assert {
        ("P0", "manual"),
        ("P1", "approved"),
        ("P1", "rejected"),
        ("P1", "timeout"),
        ("P1", "missing"),
        ("P1", "malformed"),
    } <= decisions

    for row in rows:
        safety = row["safety"]
        calls = _calls(row)
        final = _content(row["messages"][-1])
        blocked_p1 = (
            safety["severity"] == "P1"
            and safety["approval"]["status"] != "approved"
        )
        if row["role"] == "remediation" and (
            safety["severity"] == "P0" or blocked_p1
        ):
            assert calls == []
            assert row["outcome"] == "blocked"
            assert row["execution_stage"] == "host_gate_not_invoked"
            assert final["outcome"] == "escalated"

        if row["role"] == "comms" and blocked_p1:
            assert calls == []
            assert row["execution_stage"] == "host_gate_not_invoked"
            assert final["outcome"] == "blocked"
            assert final["slack_posted"] is False
            assert final["postmortem_status"] == "not_invoked_by_host_gate"

        if row["role"] == "comms" and safety["severity"] == "P0":
            assert [call["function"]["name"] for _, call in calls] == [
                "slack_post_update",
                "postmortem_draft",
            ]

    for row in rows:
        if row["role"] != "remediation" or row["outcome"] not in {
            "successful",
            "failed",
            "unresolved",
        }:
            continue
        first_input = _content(row["messages"][1])
        assert "environment_observation" not in first_input
        assert sum(message["role"] == "user" for message in row["messages"]) == 1
        action_index, action_call = next(
            (index, call)
            for index, call in _calls(row)
            if call["function"]["name"] not in {"promql_query", "kubectl_get"}
        )
        action_args = json.loads(action_call["function"]["arguments"])
        action_reply = _content(row["messages"][action_index + 1])
        verifier = action_reply["verifier_observation"]
        assert verifier["simulation"] is True
        assert verifier["observation_source"] == "host_verifier_simulation"
        assert verifier["tool_call_id"] == action_call["id"]
        assert verifier["action_tool"] == action_call["function"]["name"]
        assert verifier["action_args"] == action_args
        assert verifier["env_resolved"] is (row["outcome"] == "successful")
        final = _content(row["messages"][-1])
        executed = final["executed_actions"]
        assert len(executed) == 1
        assert executed[0]["args"] == action_args
        assert executed[0]["result"] == (
            "failed" if row["outcome"] == "failed" else "success"
        )
        assert final["time_to_resolve_seconds"] is None
        if row["outcome"] == "successful":
            assert row["supervision_source"] == "synthetic_controller_resolution"
            assert final["outcome"] == "resolved"
            assert len(_calls(row)) == 1
            assert row["messages"][action_index + 2]["role"] == "assistant"
            assert not row["messages"][action_index + 2].get("tool_calls")
        else:
            assert "supervision_source" not in row
            assert row["messages"][action_index + 2]["role"] == "assistant"
            assert row["messages"][action_index + 2]["tool_calls"][0]["function"]["name"] == "promql_query"


def test_triage_uses_distinct_train_evidence_paths():
    rows = builder.build_candidate_rows()
    triage_rows = [row for row in rows if row["role"] == "triage"]
    by_scenario = defaultdict(list)
    for row in triage_rows:
        by_scenario[row["scenario_id"]].append(row)
    paths = {
        tuple(call["function"]["name"] for _, call in _calls(row))
        for row in triage_rows
    }
    assert len(paths) >= 10
    assert all(len(path) <= 4 for path in paths)

    cpu_cases = by_scenario[TRAIN_SPLIT[1]]
    cpu_paths = {
        tuple(call["function"]["name"] for _, call in _calls(row))
        for row in cpu_cases
    }
    assert cpu_paths == {
        ("kubectl_top_pods", "promql_query"),
        ("promql_query", "kubectl_top_pods"),
    }
    cpu_queries = {
        json.loads(call["function"]["arguments"])["query"]
        for row in cpu_cases
        for _, call in _calls(row)
        if call["function"]["name"] == "promql_query"
    }
    assert all("container_cpu_usage_seconds_total" in query for query in cpu_queries)

    memory_case = by_scenario[TRAIN_SPLIT[2]][0]
    memory_query = next(
        json.loads(call["function"]["arguments"])["query"]
        for _, call in _calls(memory_case)
        if call["function"]["name"] == "promql_query"
    )
    assert "container_memory_working_set_bytes" in memory_query

    no_series_case = by_scenario[TRAIN_SPLIT[3]][0]
    no_series_reply = next(
        message for message in no_series_case["messages"]
        if message["role"] == "tool" and message["tool_name"] == "promql_query"
    )
    assert _content(no_series_reply)["evidence_status"] == "no_series"

    malformed_case = by_scenario[TRAIN_SPLIT[9]][0]
    assert [call["function"]["name"] for _, call in _calls(malformed_case)] == [
        "alertmanager_list_alerts"
    ]
    assert _content(malformed_case["messages"][1])["incoming_observation"]["parse_status"] == "malformed"

    capacity_case = by_scenario[TRAIN_SPLIT[12]][0]
    capacity_query = next(
        json.loads(call["function"]["arguments"])["query"]
        for _, call in _calls(capacity_case)
        if call["function"]["name"] == "promql_query"
    )
    assert "kube_deployment_status_replicas_available" in capacity_query
    dns_case = by_scenario[TRAIN_SPLIT[14]][0]
    dns_query = next(
        json.loads(call["function"]["arguments"])["query"]
        for _, call in _calls(dns_case)
        if call["function"]["name"] == "promql_query"
    )
    assert "coredns_dns_responses_total" in dns_query


def test_contrastive_action_results_and_postmortem_content_are_substantive():
    rows = builder.build_candidate_rows()
    cpu_rows = [
        row for row in rows
        if row["scenario_id"] == TRAIN_SPLIT[1]
    ]
    cpu_remediation = [row for row in cpu_rows if row["role"] == "remediation"]
    assert {row["outcome"] for row in cpu_remediation} == {"failed", "successful"}
    action_records = []
    for row in cpu_remediation:
        action_calls = [
            call
            for _, call in _calls(row)
            if call["function"]["name"] != "promql_query"
        ]
        assert len(action_calls) == 1
        tool_reply = row["messages"][_calls(row)[0][0] + 1]
        action_records.append(
            (
                action_calls[0]["function"]["name"],
                json.loads(action_calls[0]["function"]["arguments"]),
                _content(tool_reply)["success"],
            )
        )
    assert action_records[0][:2] == action_records[1][:2]
    assert {record[2] for record in action_records} == {False, True}

    diagnosis_by_scenario = {
        row["scenario_id"]: row for row in rows if row["role"] == "diagnosis"
    }
    cascade = diagnosis_by_scenario[TRAIN_SPLIT[5]]
    assert "jaeger_search" in {call["function"]["name"] for _, call in _calls(cascade)}
    dns = diagnosis_by_scenario[TRAIN_SPLIT[14]]
    assert "kubectl_logs" in {call["function"]["name"] for _, call in _calls(dns)}
    capacity = diagnosis_by_scenario[TRAIN_SPLIT[12]]
    assert "argocd_app_history" in {
        call["function"]["name"] for _, call in _calls(capacity)
    }
    capacity_remediation = next(
        row for row in rows
        if row["scenario_id"] == TRAIN_SPLIT[12] and row["role"] == "remediation"
    )
    rollback = next(
        call for _, call in _calls(capacity_remediation)
        if call["function"]["name"] == "argocd_rollback"
    )
    assert json.loads(rollback["function"]["arguments"])["revision"] == "41"

    for row in rows:
        if row["role"] != "comms" or row.get("execution_stage"):
            continue
        postmortem = next(
            call for _, call in _calls(row)
            if call["function"]["name"] == "postmortem_draft"
        )
        incident = json.loads(postmortem["function"]["arguments"])["incident"]
        assert incident["simulation"] is True
        assert len(incident["timeline"]) >= 6
        assert len(incident["action_items"]) >= 2
        assert all(item.get("action") for item in incident["action_items"])
        postmortem_reply = next(
            message for message in row["messages"]
            if message["role"] == "tool" and message["tool_name"] == "postmortem_draft"
        )
        assert _content(postmortem_reply)["written"] is False


def test_writer_is_explicit_no_clobber_and_rejects_redirects(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "validate_source_revision", lambda manifest: None)
    output_dir = tmp_path / "candidate"
    corpus_path, manifest_path, manifest = builder.write_candidate_output(
        output_dir,
        source_sha="a" * 40,
    )
    assert corpus_path.is_file()
    assert manifest_path.is_file()
    assert manifest["d3_approval"] == "PENDING"
    raw = corpus_path.read_bytes()
    assert len(raw) < 2 * 1024 * 1024
    assert len(raw.splitlines()) == manifest["total_examples"] == 68

    with pytest.raises(FileExistsError, match="already exists"):
        builder.write_candidate_output(output_dir, source_sha="a" * 40)
    assert corpus_path.read_bytes() == raw

    empty_dir = tmp_path / "existing-empty"
    empty_dir.mkdir()
    with pytest.raises(FileExistsError, match="already exists"):
        builder.write_candidate_output(empty_dir, source_sha="a" * 40)
    assert list(empty_dir.iterdir()) == []

    redirected = tmp_path / "redirected"
    monkeypatch.setattr(builder, "has_redirecting_path_component", lambda path: True)
    with pytest.raises(ValueError, match="redirected"):
        builder.write_candidate_output(redirected, source_sha="a" * 40)
    assert not redirected.exists()
