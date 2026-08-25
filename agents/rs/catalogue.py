"""Canonical remediation runbooks derived only from registered AtlasOps tools.

Every ``tool_name`` below is in the Remediation role ACL. Parameter templates use
placeholders; rendering is deferred to the future GRPO/remediation integration,
after approval. No module in this package executes tools.
"""

from agents.rs.schemas import Runbook, validate_catalogue
from agents.tool_policy import ROLE_ALLOWED_TOOLS


def _runbook(action_id: str, **kwargs: object) -> Runbook:
    return Runbook(action_id=action_id, **kwargs)  # type: ignore[arg-type]


def _build_catalogue() -> list[Runbook]:
    common_prereqs = ("service", "namespace")
    items: list[Runbook] = [
        _runbook(
            "inspect_workloads",
            name="Inspect affected Kubernetes workloads",
            tool_name="kubectl_get",
            parameter_template={"resource": "pods", "namespace": "{{namespace}}"},
            applicable_fault_types=("unknown", "pod_crash", "resource_saturation", "network", "io", "clock_skew"),
            prerequisites=common_prereqs,
            risk="low",
            mutating=False,
            description="Confirm workload readiness and labels before selecting a mutation.",
            stage="diagnostic",
            tags=("evidence", "read_only"),
        ),
        _runbook(
            "describe_affected_workload",
            name="Describe affected workload",
            tool_name="kubectl_describe",
            parameter_template={
                "resource": "{{workload_kind}}",
                "name": "{{service}}",
                "namespace": "{{namespace}}",
            },
            applicable_fault_types=("unknown", "configuration_regression", "scheduling", "pod_crash"),
            prerequisites=("workload_kind", *common_prereqs),
            risk="low",
            mutating=False,
            description="Collect events and conditions for the affected Deployment.",
            stage="diagnostic",
            tags=("evidence",),
        ),
        _runbook(
            "query_current_service_signal",
            name="Query current PromQL signal",
            tool_name="promql_query",
            parameter_template={"query": "{{promql_query}}"},
            applicable_fault_types=("error_rate", "latency", "resource_saturation", "traffic_surge"),
            prerequisites=("promql_query",),
            risk="low",
            mutating=False,
            description="Measure the current symptom before and after candidate selection.",
            stage="diagnostic",
            tags=("metrics",),
        ),
        _runbook(
            "notify_operator_of_candidate_plan",
            name="Notify operator of recommended plan",
            tool_name="slack_post_update",
            parameter_template={
                "channel": "#incident-response",
                "severity": "{{severity}}",
                "title": "AtlasOps recommended remediation",
                "summary": "{{recommendation_summary}}",
                "action_items": ["Review Top-K packet before approval"],
            },
            applicable_fault_types=("all",),
            prerequisites=("severity", "recommendation_summary"),
            risk="low",
            mutating=False,
            description="Publish a human-readable recommendation without cluster mutation.",
            stage="remediation",
            tags=("communication",),
        ),
        _runbook(
            "verify_readiness_after_action",
            name="Verify readiness after action",
            tool_name="kubectl_get",
            parameter_template={"resource": "deployments", "namespace": "{{namespace}}"},
            applicable_fault_types=("all",),
            prerequisites=common_prereqs,
            risk="low",
            mutating=False,
            description="Check whether Deployments returned to a ready state.",
            stage="verification",
        ),
        _runbook(
            "describe_after_action",
            name="Describe workload after action",
            tool_name="kubectl_describe",
            parameter_template={
                "resource": "deployment",
                "name": "{{service}}",
                "namespace": "{{namespace}}",
            },
            applicable_fault_types=("all",),
            prerequisites=common_prereqs,
            risk="low",
            mutating=False,
            description="Look for post-action scheduling or rollout events.",
            stage="verification",
        ),
        _runbook(
            "verify_signal_recovery",
            name="Verify recovery with PromQL",
            tool_name="promql_query",
            parameter_template={"query": "{{post_action_promql_query}}"},
            applicable_fault_types=("all",),
            prerequisites=("post_action_promql_query",),
            risk="low",
            mutating=False,
            description="Compare the objective service signal after an approved action.",
            stage="verification",
        ),
    ]

    chaos_specs = (
        ("stop_pod_chaos", "PodChaos", "pod_crash"),
        ("stop_stress_chaos", "StressChaos", "cpu_saturation"),
        ("stop_network_chaos", "NetworkChaos", "network_partition"),
        ("stop_dns_chaos", "DNSChaos", "dns_failure"),
        ("stop_io_chaos", "IOChaos", "disk_fault"),
        ("stop_time_chaos", "TimeChaos", "clock_skew"),
    )
    for action_id, kind, fault in chaos_specs:
        items.append(
            _runbook(
                action_id,
                name=f"Stop injected {kind} experiment",
                tool_name="chaos_stop_experiment",
                parameter_template={
                    "kind": kind,
                    "name": "{{chaos_resource_name}}",
                    "namespace": "chaos-mesh",
                },
                applicable_fault_types=(fault,),
                prerequisites=("chaos_resource_name", "active_chaos_experiment"),
                risk="medium",
                mutating=True,
                description=f"Remove an active {kind} resource after confirming it is the causal fault.",
                tags=("chaos_mesh", "mutation"),
            )
        )

    scale_specs = (
        ("scale_up_cpu_saturation", "cpu_saturation", "Increase replicas to absorb sustained CPU saturation.", 4),
        ("scale_up_memory_pressure", "memory_pressure", "Add capacity while memory pressure is investigated.", 4),
        ("scale_up_traffic_surge", "traffic_surge", "Scale out for demand-driven saturation.", 6),
        ("scale_up_queue_backlog", "queue_backlog", "Increase consumers to reduce an actionable backlog.", 5),
        ("quarantine_bad_deployment_scale_zero", "configuration_regression", "Temporarily remove traffic from a bad Deployment pending rollback.", 0),
    )
    for action_id, fault, description, default_replicas in scale_specs:
        items.append(
            _runbook(
                action_id,
                name=f"Scale affected deployment ({fault})",
                tool_name="kubectl_scale",
                parameter_template={
                    "deployment": "{{service}}",
                    "replicas": f"{{{{target_replicas|int:{default_replicas}}}}}",
                },
                applicable_fault_types=(fault,),
                prerequisites=("target_replicas:int", *common_prereqs),
                risk="high",
                mutating=True,
                description=description,
                tags=("capacity", "mutation"),
            )
        )

    rollout_specs = (
        ("rollout_undo_configuration_regression", "configuration_regression", "Undo the latest rollout after a bad release."),
        ("rollout_undo_dependency_mismatch", "dependency_mismatch", "Undo when a compatible release removed a new dependency assumption."),
        ("rollout_undo_error_rate_regression", "error_rate", "Undo a release associated with elevated errors."),
        ("rollout_undo_latency_regression", "latency", "Undo a release associated with latency growth."),
    )
    for action_id, fault, description in rollout_specs:
        items.append(
            _runbook(
                action_id,
                name=f"Rollout undo ({fault})",
                tool_name="kubectl_rollout",
                parameter_template={
                    "action": "undo",
                    "resource": "deployment/{{service}}",
                    "namespace": "{{namespace}}",
                },
                applicable_fault_types=(fault,),
                prerequisites=("deployment_recently_changed", *common_prereqs),
                risk="high",
                mutating=True,
                description=description,
                tags=("release", "mutation"),
            )
        )

    argo_specs = (
        ("argocd_rollback_bad_manifest", "configuration_regression", "bad_manifest", "Roll back an Argo CD application using its prior revision ID."),
        ("argocd_rollback_partial_deploy", "partial_deployment", "partial_deploy", "Restore consistency after a partial Argo CD rollout."),
        ("argocd_rollback_release_errors", "error_rate", "recent_release_errors", "Roll back when errors correlate with a recent application revision."),
    )
    for action_id, fault, revision_input, description in argo_specs:
        items.append(
            _runbook(
                action_id,
                name=f"Argo CD rollback ({fault})",
                tool_name="argocd_rollback",
                parameter_template={
                    "app": "{{argocd_app}}",
                    "revision": f"{{{{{revision_input}|int}}}}",
                },
                applicable_fault_types=(fault,),
                prerequisites=("argocd_app", f"{revision_input}:int", "revision_history_available"),
                risk="high",
                mutating=True,
                description=description,
                tags=("argocd", "release", "mutation"),
            )
        )

    silence_specs = (
        ("silence_flapping_alert_during_mitigation", "flapping_alert"),
        ("silence_noisy_duplicate_alert", "duplicate_alert_noise"),
    )
    for action_id, fault in silence_specs:
        items.append(
            _runbook(
                action_id,
                name=f"Silence alert ({fault})",
                tool_name="alertmanager_silence",
                parameter_template={
                    "matchers": [{"name": "alertname", "value": "{{alertname}}", "isRegex": False}],
                    "duration_minutes": "{{duration_minutes|int:30}}",
                    "comment": "AtlasOps temporary mitigation silence",
                    "created_by": "atlasops-recommender",
                },
                applicable_fault_types=(fault,),
                prerequisites=("alertname", "duration_minutes:int", "mitigation_in_progress"),
                risk="medium",
                mutating=True,
                description="Temporarily suppress known duplicate noise while mitigation proceeds; this does not resolve root cause.",
                tags=("alerting", "mutation"),
            )
        )
    return items


RUNBOOK_CATALOGUE: list[Runbook] = _build_catalogue()
validate_catalogue(RUNBOOK_CATALOGUE, ROLE_ALLOWED_TOOLS["remediation"])
