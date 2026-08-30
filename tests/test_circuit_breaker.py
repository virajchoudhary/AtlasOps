"""Tests for circuit breaker behavior."""

import pytest


def test_tool_call_limit_trips_for_incident():
    from agents.circuit_breaker import CircuitBreaker, CircuitBreakerTripped

    cb = CircuitBreaker(max_tool_calls_per_incident=2)
    cb.start_incident()
    cb.check_before_tool_call("inc-1", "kubectl_get", is_cluster_mutating=False)
    cb.check_before_tool_call("inc-1", "kubectl_get", is_cluster_mutating=False)
    with pytest.raises(CircuitBreakerTripped):
        cb.check_before_tool_call("inc-1", "kubectl_get", is_cluster_mutating=False)
    cb.finish_incident("inc-1", resolved=True)


def test_cluster_mutating_action_hourly_limit():
    from agents.circuit_breaker import CircuitBreaker, CircuitBreakerTripped

    cb = CircuitBreaker(max_cluster_mutating_actions_per_hour=1)
    cb.start_incident()
    cb.check_before_tool_call("inc-2", "kubectl_scale", is_cluster_mutating=True)
    with pytest.raises(CircuitBreakerTripped):
        cb.check_before_tool_call("inc-2", "kubectl_scale", is_cluster_mutating=True)
    cb.finish_incident("inc-2", resolved=True)


def test_non_cluster_side_effect_does_not_consume_cluster_quota():
    from agents.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(max_cluster_mutating_actions_per_hour=1)
    cb.start_incident()
    cb.check_before_tool_call("inc-comms", "slack_post_update", is_cluster_mutating=False)
    cb.check_before_tool_call("inc-comms", "postmortem_draft", is_cluster_mutating=False)

    status = cb.status()
    assert status["cluster_mutating_actions_this_hour"] == 0
    assert status["max_cluster_mutating_actions_per_hour"] == 1
    cb.finish_incident("inc-comms", resolved=True)


def test_failed_mutation_refunds_the_blast_radius_reservation():
    """A wrapper that changed nothing must not consume the hourly quota.

    Gate G4 run 008 spent nine of ten hourly cluster mutations on
    argocd_rollback calls that all failed at the transport layer, leaving no
    budget for a remediation that would actually have changed the cluster.
    """
    from agents.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(max_cluster_mutating_actions_per_hour=2)
    cb.start_incident()
    for _ in range(9):
        cb.check_before_tool_call("inc-008", "argocd_rollback", is_cluster_mutating=True)
        cb.release_cluster_mutation_reservation()  # every call failed

    assert cb.status()["cluster_mutating_actions_this_hour"] == 0
    # Budget survives for the action that does change the cluster.
    cb.check_before_tool_call("inc-008", "chaos_stop_experiment", is_cluster_mutating=True)
    assert cb.status()["cluster_mutating_actions_this_hour"] == 1
    cb.finish_incident("inc-008", resolved=True)


def test_successful_mutations_still_consume_quota():
    from agents.circuit_breaker import CircuitBreaker, CircuitBreakerTripped

    cb = CircuitBreaker(max_cluster_mutating_actions_per_hour=2)
    cb.start_incident()
    cb.check_before_tool_call("inc-real", "kubectl_scale", is_cluster_mutating=True)
    cb.check_before_tool_call("inc-real", "kubectl_scale", is_cluster_mutating=True)
    with pytest.raises(CircuitBreakerTripped):
        cb.check_before_tool_call("inc-real", "kubectl_scale", is_cluster_mutating=True)
    cb.finish_incident("inc-real", resolved=True)


def test_reservation_refund_never_goes_negative():
    from agents.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker()
    cb.release_cluster_mutation_reservation()
    cb.release_cluster_mutation_reservation()
    assert cb.status()["cluster_mutating_actions_this_hour"] == 0


def test_consecutive_system_errors_trip_breaker():
    from agents.circuit_breaker import CircuitBreaker, CircuitBreakerTripped

    cb = CircuitBreaker(consecutive_failure_threshold=2)
    cb.start_incident()
    cb.finish_incident("inc-a", resolved=False, reason="system_error")
    cb.start_incident()
    cb.finish_incident("inc-b", resolved=False, reason="system_error")
    with pytest.raises(CircuitBreakerTripped):
        cb.start_incident()


def test_approval_rejected_does_not_trip_breaker():
    """Judges rejecting remediation is a designed outcome, not a system failure."""
    from agents.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(consecutive_failure_threshold=2)
    for i in range(5):
        cb.start_incident()
        cb.finish_incident(f"inc-rej-{i}", resolved=False, reason="approval_rejected")
    assert cb.status()["tripped"] is False
    assert cb.status()["consecutive_failures"] == 0


def test_manual_runbook_does_not_trip_breaker():
    from agents.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(consecutive_failure_threshold=2)
    for i in range(5):
        cb.start_incident()
        cb.finish_incident(f"inc-man-{i}", resolved=False, reason="manual_runbook")
    assert cb.status()["tripped"] is False


def test_approval_timeout_does_not_trip_breaker():
    from agents.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(consecutive_failure_threshold=2)
    for i in range(5):
        cb.start_incident()
        cb.finish_incident(f"inc-to-{i}", resolved=False, reason="approval_timeout")
    assert cb.status()["tripped"] is False


def test_unresolved_without_reason_still_trips():
    """Plain unresolved (no reason) should trip the breaker for backward compat."""
    from agents.circuit_breaker import CircuitBreaker, CircuitBreakerTripped

    cb = CircuitBreaker(consecutive_failure_threshold=2)
    cb.start_incident()
    cb.finish_incident("inc-x", resolved=False)
    cb.start_incident()
    cb.finish_incident("inc-y", resolved=False)
    with pytest.raises(CircuitBreakerTripped):
        cb.start_incident()


def test_reset_clears_tripped_state():
    from agents.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(consecutive_failure_threshold=1)
    cb.start_incident()
    cb.finish_incident("inc-c", resolved=False, reason="system_error")
    assert cb.status()["tripped"] is True
    status = cb.reset()
    assert status["tripped"] is False
    assert status["active_incidents"] == 0


def test_resolved_resets_consecutive_failures():
    from agents.circuit_breaker import CircuitBreaker

    cb = CircuitBreaker(consecutive_failure_threshold=3)
    cb.start_incident()
    cb.finish_incident("inc-1", resolved=False, reason="system_error")
    cb.start_incident()
    cb.finish_incident("inc-2", resolved=False, reason="system_error")
    assert cb.status()["consecutive_failures"] == 2
    cb.start_incident()
    cb.finish_incident("inc-3", resolved=True)
    assert cb.status()["consecutive_failures"] == 0


def test_investigation_cannot_starve_remediation():
    """Remediation is the only role that can resolve, and it runs last.

    In Gate G4 run 012 triage and diagnosis consumed all 50 per-incident tool
    calls, so every remediation call — including read-only chaos discovery — was
    refused and the agent escalated against a fault it had the tools to fix.
    """
    from agents.circuit_breaker import CircuitBreaker, CircuitBreakerTripped

    cb = CircuitBreaker(max_tool_calls_per_incident=50, reserved_remediation_tool_calls=12)
    cb.start_incident()

    investigation_calls = 0
    with pytest.raises(CircuitBreakerTripped, match="reserved for remediation"):
        while True:
            cb.check_before_tool_call("inc", "kubectl_get", is_cluster_mutating=False, role="diagnosis")
            investigation_calls += 1
    assert investigation_calls == 38

    remediation_calls = 0
    try:
        while True:
            cb.check_before_tool_call("inc", "chaos_list_experiments", is_cluster_mutating=False,
                                      role="remediation")
            remediation_calls += 1
    except CircuitBreakerTripped:
        pass
    assert remediation_calls == 12
    cb.finish_incident("inc", resolved=True)


def test_reservation_message_tells_the_agent_what_to_do():
    from agents.circuit_breaker import CircuitBreaker, CircuitBreakerTripped

    cb = CircuitBreaker(max_tool_calls_per_incident=4, reserved_remediation_tool_calls=2)
    cb.start_incident()
    with pytest.raises(CircuitBreakerTripped) as excinfo:
        for _ in range(5):
            cb.check_before_tool_call("inc", "promql_query", is_cluster_mutating=False, role="triage")
    assert "Produce your conclusion" in str(excinfo.value)
    cb.finish_incident("inc", resolved=False, reason="escalation")


def test_remediation_still_bounded_by_the_overall_limit():
    """The reservation is a floor for remediation, not an exemption."""
    from agents.circuit_breaker import CircuitBreaker, CircuitBreakerTripped

    cb = CircuitBreaker(max_tool_calls_per_incident=3, reserved_remediation_tool_calls=1)
    cb.start_incident()
    for _ in range(3):
        cb.check_before_tool_call("inc", "kubectl_get", is_cluster_mutating=False, role="remediation")
    with pytest.raises(CircuitBreakerTripped):
        cb.check_before_tool_call("inc", "kubectl_get", is_cluster_mutating=False, role="remediation")
    cb.finish_incident("inc", resolved=True)


def test_unlabelled_calls_keep_the_original_limit():
    """Callers that pass no role must behave exactly as before."""
    from agents.circuit_breaker import CircuitBreaker, CircuitBreakerTripped

    cb = CircuitBreaker(max_tool_calls_per_incident=3, reserved_remediation_tool_calls=2)
    cb.start_incident()
    for _ in range(3):
        cb.check_before_tool_call("inc", "kubectl_get", is_cluster_mutating=False)
    with pytest.raises(CircuitBreakerTripped):
        cb.check_before_tool_call("inc", "kubectl_get", is_cluster_mutating=False)
    cb.finish_incident("inc", resolved=True)


def test_coordinator_passes_the_role_to_the_breaker():
    """The reservation is inert unless call_agent actually labels the call."""
    import inspect

    import agents.coordinator as coordinator

    source = inspect.getsource(coordinator.call_agent)
    assert "role=role," in source.split("check_before_tool_call(")[1].split(")")[0]
