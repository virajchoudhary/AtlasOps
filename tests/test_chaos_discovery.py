"""Contract tests for chaos experiment discovery and model-facing result compaction.

Gate G4 failed eight consecutive times against a goal state no agent could
observe: every frozen scenario requires Chaos Mesh clearance, but
``chaos_stop_experiment`` needs an exact resource name that nothing in the
runtime could report. These tests pin the two repairs — a read-only discovery
wrapper for remediation, and list-result projection so generic ``kubectl_get``
discovery survives the model's context budget.
"""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest


def _kubectl_result(payload: dict, returncode: int = 0, stderr: str = "") -> MagicMock:
    return MagicMock(returncode=returncode, stdout=json.dumps(payload), stderr=stderr)


_STRESSCHAOS_ITEM = {
    "kind": "StressChaos",
    "metadata": {
        "name": "sf-002-paymentservice-cpu",
        "namespace": "chaos-mesh",
        "creationTimestamp": "2026-08-25T03:44:42Z",
    },
    "spec": {
        "mode": "all",
        "duration": "10m",
        "selector": {"namespaces": ["default"], "labelSelectors": {"app": "paymentservice"}},
        "stressors": {"cpu": {"workers": 2, "load": 100}},
    },
    "status": {"experiment": {"desiredPhase": "Run"}},
}


class TestChaosListExperiments:
    def test_reports_exact_name_required_by_stop(self):
        from agents.tools.chaos import chaos_list_experiments

        with patch("subprocess.run", return_value=_kubectl_result({"items": [_STRESSCHAOS_ITEM]})):
            result = chaos_list_experiments()

        assert result["success"] is True
        assert result["count"] == 1
        experiment = result["experiments"][0]
        # The exact (kind, name, namespace) triple chaos_stop_experiment requires.
        assert experiment["kind"] == "StressChaos"
        assert experiment["name"] == "sf-002-paymentservice-cpu"
        assert experiment["namespace"] == "chaos-mesh"
        assert experiment["target"]["app"] == "paymentservice"

    def test_discovered_name_is_accepted_by_stop_experiment(self):
        """The discovery output must satisfy the stop wrapper's own validation."""
        from agents.tools.chaos import chaos_list_experiments, chaos_stop_experiment

        with patch("subprocess.run", return_value=_kubectl_result({"items": [_STRESSCHAOS_ITEM]})):
            discovered = chaos_list_experiments()["experiments"][0]

        delete_result = MagicMock(returncode=0, stdout="stresschaos.chaos-mesh.org deleted", stderr="")
        with patch("subprocess.run", return_value=delete_result) as run:
            stopped = chaos_stop_experiment(
                kind=discovered["kind"],
                name=discovered["name"],
                namespace=discovered["namespace"],
            )

        assert stopped["success"] is True
        assert "sf-002-paymentservice-cpu" in run.call_args[0][0]

    def test_queries_every_allowed_chaos_kind(self):
        from agents.tools.chaos import ALLOWED_CHAOS_KINDS, chaos_list_experiments

        with patch("subprocess.run", return_value=_kubectl_result({"items": []})) as run:
            chaos_list_experiments()

        # Locate the resource argument by content: _run may prepend --context.
        argv = run.call_args[0][0]
        resource_args = [a for a in argv if "," in a]
        assert len(resource_args) == 1
        assert set(resource_args[0].split(",")) == set(ALLOWED_CHAOS_KINDS)

    def test_all_namespaces_by_default(self):
        from agents.tools.chaos import chaos_list_experiments

        with patch("subprocess.run", return_value=_kubectl_result({"items": []})) as run:
            chaos_list_experiments()
        assert "-A" in run.call_args[0][0]

    def test_rejects_malformed_namespace(self):
        from agents.tools.chaos import chaos_list_experiments

        result = chaos_list_experiments(namespace="--kubeconfig=/etc/shadow")
        assert result["success"] is False
        assert "Invalid namespace" in result["error"]

    def test_surfaces_query_failure(self):
        from agents.tools.chaos import chaos_list_experiments

        with patch("subprocess.run", return_value=MagicMock(returncode=1, stdout="", stderr="no CRD")):
            result = chaos_list_experiments()
        assert result["success"] is False
        assert "no CRD" in result["error"]

    def test_empty_cluster_is_success_not_failure(self):
        from agents.tools.chaos import chaos_list_experiments

        with patch("subprocess.run", return_value=_kubectl_result({"items": []})):
            result = chaos_list_experiments()
        assert result["success"] is True
        assert result["count"] == 0


class TestModelFacingToolOutput:
    def test_crd_discovery_survives_the_context_budget(self):
        """A raw CRD dump truncates to schema padding; the projection stays legible."""
        from agents.coordinator import _MODEL_TOOL_RESULT_CHAR_CAP, _model_facing_tool_output

        items = [
            {
                "kind": "CustomResourceDefinition",
                "metadata": {"name": f"{kind}.chaos-mesh.org"},
                "spec": {
                    "group": "chaos-mesh.org",
                    "scope": "Namespaced",
                    "names": {"kind": kind.title()},
                    # Real CRDs carry their full OpenAPI schema here.
                    "versions": [{"schema": {"openAPIV3Schema": {"pad": "x" * 40000}}}],
                },
            }
            for kind in ("stresschaos", "podchaos", "networkchaos")
        ]
        raw = {"success": True, "stdout": json.dumps({"items": items}), "parsed": {"items": items}}

        assert len(json.dumps(raw)) > _MODEL_TOOL_RESULT_CHAR_CAP
        projected = json.dumps(_model_facing_tool_output(raw))
        assert len(projected) < _MODEL_TOOL_RESULT_CHAR_CAP
        for kind in ("stresschaos", "podchaos", "networkchaos"):
            assert f"{kind}.chaos-mesh.org" in projected
        assert "xxxx" not in projected

    def test_deployment_health_is_preserved(self):
        from agents.coordinator import _model_facing_tool_output

        raw = {
            "success": True,
            "parsed": {
                "items": [
                    {
                        "kind": "Deployment",
                        "metadata": {"name": "paymentservice", "namespace": "default"},
                        "status": {"replicas": 1, "readyReplicas": 1},
                    }
                ]
            },
        }
        projected = _model_facing_tool_output(raw)
        assert projected["items"][0] == {
            "name": "paymentservice",
            "namespace": "default",
            "kind": "Deployment",
            "ready": "1/1",
        }

    def test_non_list_results_pass_through_untouched(self):
        from agents.coordinator import _model_facing_tool_output

        raw = {"success": True, "result": [{"value": [0, "1"]}]}
        assert _model_facing_tool_output(raw) is raw

    def test_verifier_still_reads_the_untouched_payload(self):
        """Projection is model-facing only; programmatic callers keep full JSON."""
        from agents.verifier import EnvironmentVerifier, WorkloadPredicate

        payload = {
            "items": [
                {
                    "kind": "Deployment",
                    "metadata": {"name": "paymentservice", "namespace": "default"},
                    "status": {"replicas": 1, "readyReplicas": 1, "availableReplicas": 1},
                }
            ]
        }
        # Drive the real kubectl_get wrapper over a stubbed subprocess, so the
        # test covers the actual path the verifier uses in production rather
        # than a lambda that trivially returns the answer.
        from agents.tools.kubectl import kubectl_get

        with patch("subprocess.run", return_value=_kubectl_result(payload)):
            verifier = EnvironmentVerifier(kubectl_getter=kubectl_get)
            check = verifier._verify_workload(WorkloadPredicate(name="paymentservice"))
        assert check.passed is True
        assert check.observed == {"ready_replicas": 1, "desired_replicas": 1}

        # The same payload, once projected for the model, no longer carries the
        # replica structure the verifier depends on — which is exactly why the
        # projection must never be applied to programmatic callers.
        from agents.coordinator import _model_facing_tool_output

        projected = _model_facing_tool_output({"success": True, "parsed": payload})
        assert "parsed" not in projected


class TestRepeatedFailureGuard:
    def test_signature_prefers_error_class(self):
        from agents.coordinator import _tool_error_signature

        assert _tool_error_signature({"success": False, "error_class": "not_found"}) == "not_found"

    def test_same_cause_under_differing_arguments_shares_one_signature(self):
        """Run 008 cycled nine revisions against one unchanging transport error.

        The arguments differ; the cause does not. The guard keys on cause.
        """
        from agents.coordinator import _tool_error_signature

        # One output per attempted revision — same failure, different call.
        outputs = [
            {
                "success": False,
                "error": "argocd_request_error: request failed",
                "error_class": "request_failed",
                "attempted_revision": revision,
            }
            for revision in ("latest", "previous", "1", "0", "-1", "0", "-2", "-3", "-4")
        ]
        assert len({_tool_error_signature(o) for o in outputs}) == 1

    def test_unrelated_kubectl_failures_get_distinct_signatures(self):
        """Distinct causes must not collapse, or the guard disables a live tool.

        kubectl wrappers return {stdout, stderr, returncode, success} with no
        `error` key. A literal fallback made every kubectl failure identical, so
        two unrelated errors — a missing pod and an unreachable apiserver —
        would retire `kubectl_get` for the rest of the incident.
        """
        from agents.coordinator import _tool_error_signature

        not_found = {
            "stdout": "", "returncode": 1, "success": False,
            "stderr": 'Error from server (NotFound): pods "cartservice-x" not found',
        }
        unreachable = {
            "stdout": "", "returncode": 1, "success": False,
            "stderr": "Unable to connect to the server: dial tcp i/o timeout",
        }
        forbidden = {
            "stdout": "", "returncode": 1, "success": False,
            "stderr": 'Error from server (Forbidden): pods is forbidden',
        }
        signatures = {
            _tool_error_signature(not_found),
            _tool_error_signature(unreachable),
            _tool_error_signature(forbidden),
        }
        assert len(signatures) == 3, f"causes collapsed to {signatures}"
        assert all(s is not None for s in signatures)

    def test_identical_kubectl_failures_still_share_a_signature(self):
        """The guard must still fire on a genuinely repeating failure."""
        from agents.coordinator import _tool_error_signature

        same = {
            "stdout": "", "returncode": 1, "success": False,
            "stderr": "Unable to connect to the server: dial tcp i/o timeout",
        }
        assert _tool_error_signature(same) == _tool_error_signature(dict(same))

    def test_success_has_no_signature(self):
        from agents.coordinator import _tool_error_signature

        assert _tool_error_signature({"success": True, "count": 0}) is None

    def test_limit_fires_before_every_per_tool_cap(self):
        """The guard must fire before the generic cap, whatever the cap is set to.

        Comparing against a hardcoded 8 could not detect a cap being lowered, and
        ignored the per-tool caps entirely (promql_query is 6, kubectl_logs 4).
        """
        import inspect

        import agents.coordinator as coordinator

        source = inspect.getsource(coordinator.call_agent)
        caps_literal = source.split("_TOOL_CAPS = ")[1].split("\n")[0].rstrip()
        caps = eval(caps_literal)  # noqa: S307 - literal dict from our own source
        default_cap = int(source.split('_cap = _TOOL_CAPS.get(fn_name, ')[1].split(")")[0])

        for cap in list(caps.values()) + [default_cap]:
            assert coordinator._REPEATED_FAILURE_LIMIT < cap


class TestKubectlArgumentInjection:
    """Model-supplied tool arguments must not become kubectl flags.

    There is no shell, so metacharacters are inert — but kubectl parses any argv
    element starting with '-' as a flag, so an unvalidated argument could reach
    resources and identities the role ACL never granted.
    """

    @pytest.mark.parametrize(
        "resource",
        [
            "--raw=/api/v1/namespaces/kube-system/secrets",
            "--kubeconfig=/tmp/evil.yaml",
            "--as=system:admin",
            "-n kube-system secrets",
            "pods --all-namespaces",
        ],
    )
    def test_get_rejects_flag_shaped_resources(self, resource):
        from agents.tools.kubectl import kubectl_get

        with patch("subprocess.run") as run:
            result = kubectl_get(resource)
        assert result["success"] is False
        assert "Invalid resource" in result["error"]
        run.assert_not_called()

    def test_get_rejects_flag_shaped_output_format(self):
        from agents.tools.kubectl import kubectl_get

        with patch("subprocess.run") as run:
            result = kubectl_get("pods", output="jsonpath={.items[*]}")
        assert result["success"] is False
        run.assert_not_called()

    def test_get_rejects_flag_shaped_namespace(self):
        from agents.tools.kubectl import kubectl_get

        with patch("subprocess.run") as run:
            result = kubectl_get("pods", namespace="--as=system:admin")
        assert result["success"] is False
        run.assert_not_called()

    @pytest.mark.parametrize(
        "resource",
        [
            "pods",
            "deployment",
            "customresourcedefinitions",
            "podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos",
            "deployment/paymentservice",
            "podchaos.chaos-mesh.org",
        ],
    )
    def test_legitimate_resources_still_pass(self, resource):
        from agents.tools.kubectl import kubectl_get

        with patch("subprocess.run", return_value=_kubectl_result({"items": []})) as run:
            result = kubectl_get(resource)
        assert result["success"] is True
        run.assert_called_once()

    def test_logs_rejects_flag_shaped_pod(self):
        from agents.tools.kubectl import kubectl_logs

        with patch("subprocess.run") as run:
            assert kubectl_logs("--kubeconfig=/tmp/x")["success"] is False
        run.assert_not_called()

    def test_scale_rejects_flag_shaped_deployment(self):
        from agents.tools.kubectl import kubectl_scale

        with patch("subprocess.run") as run:
            assert kubectl_scale("--all", 1)["success"] is False
        run.assert_not_called()

    def test_describe_rejects_flag_shaped_name(self):
        from agents.tools.kubectl import kubectl_describe

        with patch("subprocess.run") as run:
            assert kubectl_describe("pods", "--all-namespaces")["success"] is False
        run.assert_not_called()

    def test_verifier_queries_still_validate(self):
        """The verifier's own cluster-wide chaos query must remain legal."""
        from agents.tools.chaos import CHAOS_RESOURCE_TYPES
        from agents.tools.kubectl import _validate_resource

        assert _validate_resource(CHAOS_RESOURCE_TYPES) is None


class TestProjectionRespectsTheModelBudget:
    """Projecting a fixed item count is not enough to stay inside the budget."""

    def test_large_list_stays_within_the_cap_and_valid_json(self):
        from agents.coordinator import _MODEL_TOOL_RESULT_CHAR_CAP, _model_facing_tool_output

        items = [
            {
                "kind": "Pod",
                "metadata": {"name": f"productcatalogservice-{i}-7d9f8b", "namespace": "default"},
                "status": {"phase": "Running",
                           "containerStatuses": [{"ready": True, "restartCount": 0}]},
            }
            for i in range(1000)
        ]
        projected = _model_facing_tool_output({"success": True, "parsed": {"items": items}})
        serialized = json.dumps(projected)

        assert len(serialized) <= _MODEL_TOOL_RESULT_CHAR_CAP
        # Whatever the model receives must parse. A hard slice produced a
        # truncated object that json.loads could not read at all.
        assert json.loads(serialized)["item_count"] == 1000

    def test_dropped_items_are_declared(self):
        from agents.coordinator import _model_facing_tool_output

        items = [
            {"kind": "Pod", "metadata": {"name": f"pod-{i}", "namespace": "default"},
             "status": {"phase": "Running"}}
            for i in range(1000)
        ]
        projected = _model_facing_tool_output({"success": True, "parsed": {"items": items}})
        assert projected["items_truncated"] is True
        assert projected["items_shown"] < projected["item_count"]
        assert "narrow the query" in projected["truncation_note"]

    def test_small_list_is_not_marked_truncated(self):
        from agents.coordinator import _model_facing_tool_output

        items = [{"kind": "Pod", "metadata": {"name": "pod-1", "namespace": "default"}}]
        projected = _model_facing_tool_output({"success": True, "parsed": {"items": items}})
        assert projected.get("items_truncated") is None
        assert projected["items_shown"] == 1


class TestProjectionIsAppliedAtTheCallSite:
    """The pure function being correct is worth nothing if call_agent bypasses it."""

    @pytest.fixture(autouse=True)
    def _audit_env(self, monkeypatch, tmp_path):
        monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", "projection-call-site-synthetic-secret")
        monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))

    def test_tool_message_sent_to_the_model_is_the_projection(self):
        import asyncio

        from agents.coordinator import _MODEL_TOOL_RESULT_CHAR_CAP, call_agent

        items = [
            {"kind": "CustomResourceDefinition",
             "metadata": {"name": f"{k}.chaos-mesh.org"},
             "spec": {"group": "chaos-mesh.org", "names": {"kind": k.title()},
                      "versions": [{"schema": {"openAPIV3Schema": {"pad": "x" * 40000}}}]}}
            for k in ("stresschaos", "podchaos")
        ]
        huge = {"success": True, "stdout": json.dumps({"items": items}),
                "parsed": {"items": items}}

        def _response(content="", tool_calls=None):
            r = MagicMock()
            r.raise_for_status = MagicMock()
            msg = {"role": "assistant", "content": content}
            if tool_calls is not None:
                msg["tool_calls"] = tool_calls
            r.json.return_value = {"choices": [{"message": msg, "finish_reason": "stop"}]}
            return r

        call = {"id": "c1", "type": "function",
                "function": {"name": "kubectl_get", "arguments": json.dumps({"resource": "crd"})}}
        conclusion = json.dumps({"outcome": "unresolved", "proposed_actions": [],
                                 "executed_actions": [], "verified_by": "none"})

        captured = {}
        with patch("agents.coordinator.post_with_retry",
                   side_effect=[_response(tool_calls=[call]), _response(conclusion)]) as post:
            with patch("agents.coordinator.require_audit_log"):
                with patch.dict("agents.coordinator.TOOL_REGISTRY",
                                {"kubectl_get": MagicMock(return_value=huge)}):
                    asyncio.run(call_agent(
                        "remediation",
                        {"incident_id": "inc-proj", "triage": {"severity": "P1"}},
                        max_turns=2,
                    ))
            # The second model call carries the tool message.
            messages = post.call_args_list[1][0][2]["messages"]
            captured = [m for m in messages if m.get("role") == "tool"][0]

        content = captured["content"]
        assert len(content) <= _MODEL_TOOL_RESULT_CHAR_CAP
        payload = json.loads(content)          # must be parseable, not a raw slice
        assert payload["item_count"] == 2
        assert "stresschaos.chaos-mesh.org" in content
        assert "xxxx" not in content            # the schema dump never reaches the model


class TestKnownScenarioIdentityLeak:
    """Tracked limitation: chaos resource names embed their scenario ID.

    `bench/runner.py` now passes `scenario_id` out-of-band so it never reaches
    the model-visible prompt. But every frozen manifest names its resource after
    the scenario — `sf-002-paymentservice-cpu`, `cs-001-currency-latency` — and
    `chaos_stop_experiment` requires that exact name, so `chaos_list_experiments`
    necessarily returns it to the Remediation role.

    Bounded, but real:
      - Diagnosis never sees it. Root-cause accuracy, the measured quantity, is
        unaffected.
      - Remediation already knows the affected service from triage; the extra
        information is the benchmark's identifier for the scenario.
      - It matters most for a *trained* policy, which could memorise
        scenario-id -> action. No training has run, so nothing has learned it yet.

    Fixing it means renaming resources across all 28 manifests to
    non-identifying names, which changes manifest hashes and the verifier's
    diagnostic prefix matching — a Stage 5 scenario-truth decision, not a tooling
    change. These tests keep the leak surface pinned so it cannot be forgotten,
    and will fail once the manifests are renamed, prompting the docs to follow.
    """

    def _manifest_resource_names(self):
        import re
        from pathlib import Path

        root = Path(__file__).resolve().parents[1] / "bench" / "chaos_manifests"
        names = {}
        for manifest in sorted(root.rglob("*.yaml")):
            found = re.findall(r"^\s{2}name:\s*(\S+)", manifest.read_text(encoding="utf-8"), re.M)
            if found:
                names[f"{manifest.parent.name}/{manifest.stem}"] = found
        return names

    def test_leak_surface_is_known_and_unchanged(self):
        names = self._manifest_resource_names()
        assert names, "no chaos manifests found"

        leaking = {
            scenario: resources
            for scenario, resources in names.items()
            for short in [scenario.split("/")[-1]]
            if any(r.startswith(short) for r in resources)
        }
        # hist-knight-capital-2012 is the sole exception: it injects a rogue
        # Deployment (checkoutservice-legacy) rather than a Chaos Mesh
        # experiment, so it has no scenario-named chaos resource to leak.
        non_leaking = set(names) - set(leaking)
        assert non_leaking == {"named_replays/hist-knight-capital-2012"}, (
            f"leak surface changed: {sorted(non_leaking)}. If manifests were renamed, "
            "update docs/project/G4_V4_BEHAVIOUR_PROBE.md and retire this test."
        )

    def test_diagnosis_cannot_reach_the_leaking_channel(self):
        """The containment that makes the leak tolerable must hold."""
        from agents.tool_policy import ROLE_ALLOWED_TOOLS

        assert "chaos_list_experiments" not in ROLE_ALLOWED_TOOLS["diagnosis"]
        assert "chaos_list_experiments" not in ROLE_ALLOWED_TOOLS["triage"]


class TestValidatorsAcceptRealCallSites:
    """Injection guards must reject flags without rejecting legitimate usage.

    Both regressions came from using a DNS-1123 *label* pattern where Kubernetes
    uses DNS *subdomains* or qualified references.
    """

    def test_logs_accepts_the_qualified_form_the_acceptance_script_uses(self):
        """scripts/acceptance_stage3_local.py calls kubectl_logs('deployment/frontend')."""
        from agents.tools.kubectl import kubectl_logs

        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="log line", stderr="")) as run:
            result = kubectl_logs("deployment/frontend", namespace="default", tail=5)

        assert result["success"] is True
        assert "deployment/frontend" in run.call_args[0][0]

    def test_logs_still_accepts_a_bare_pod_name(self):
        from agents.tools.kubectl import kubectl_logs

        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="", stderr="")):
            assert kubectl_logs("frontend-5d9c-abcde")["success"] is True

    def test_describe_accepts_the_dotted_names_the_projection_surfaces(self):
        """kubectl_get('customresourcedefinitions') yields stresschaos.chaos-mesh.org."""
        from agents.tools.kubectl import kubectl_describe

        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="Name: x", stderr="")) as run:
            result = kubectl_describe(
                "customresourcedefinition", "stresschaos.chaos-mesh.org", namespace="default"
            )

        assert result["success"] is True
        assert "stresschaos.chaos-mesh.org" in run.call_args[0][0]

    def test_a_projected_crd_name_round_trips_into_describe(self):
        """Close the loop: whatever discovery shows the model must be callable."""
        from agents.coordinator import _model_facing_tool_output
        from agents.tools.kubectl import kubectl_describe

        crd = {
            "kind": "CustomResourceDefinition",
            "metadata": {"name": "stresschaos.chaos-mesh.org"},
            "spec": {"group": "chaos-mesh.org", "names": {"kind": "StressChaos"}, "scope": "Namespaced"},
        }
        projected = _model_facing_tool_output({"success": True, "parsed": {"items": [crd]}})
        surfaced_name = projected["items"][0]["name"]

        with patch("subprocess.run", return_value=MagicMock(returncode=0, stdout="ok", stderr="")):
            assert kubectl_describe("customresourcedefinition", surfaced_name)["success"] is True

    @pytest.mark.parametrize(
        "hostile",
        ["--kubeconfig=/tmp/x", "--all-namespaces", "-n kube-system", "../../etc/passwd",
         "frontend -n kube-system", "", "pod\nname"],
    )
    def test_hostile_values_are_still_rejected_everywhere(self, hostile):
        from agents.tools.kubectl import kubectl_describe, kubectl_logs, kubectl_scale

        with patch("subprocess.run") as run:
            assert kubectl_logs(hostile)["success"] is False
            assert kubectl_describe("pods", hostile)["success"] is False
            assert kubectl_scale(hostile, 1)["success"] is False
            run.assert_not_called()
