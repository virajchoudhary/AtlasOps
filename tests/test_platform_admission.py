"""Local contract tests for the disposable-cluster admission runner."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from scripts import verify_platform_admission as admission
from scripts import verify_platform_infrastructure as platform_verify


def local_admission_documents():
    manifest_path = (
        Path(admission.__file__).resolve().parents[1]
        / "infra"
        / "local"
        / "operational-admission.yaml"
    )
    return [
        item
        for item in admission.yaml.safe_load_all(
            manifest_path.read_text(encoding="utf-8")
        )
        if isinstance(item, dict)
    ]


@pytest.mark.parametrize("field", [
    "labels",
    "annotations",
    "ownerReferences",
    "finalizers",
    "deletionTimestamp",
    "deletionGracePeriodSeconds",
])
def test_rollback_policy_guards_optional_metadata_before_comparison(field) -> None:
    policy = next(
        item
        for item in local_admission_documents()
        if item["kind"] == "ValidatingAdmissionPolicy"
    )
    expression = policy["spec"]["validations"][0]["expression"]
    presence = f"has(object.metadata.{field}) == has(oldObject.metadata.{field})"
    comparison = (
        f"(!has(oldObject.metadata.{field}) || "
        f"object.metadata.{field} == oldObject.metadata.{field})"
    )
    assert presence in expression
    assert comparison in expression


def test_admission_runner_requires_its_explicit_disposable_github_context() -> None:
    with pytest.raises(ValueError, match="disposable context"):
        admission.validate_invocation(
            "kind-atlasops-local",
            admission.SERVICE_ACCOUNT,
            {},
        )

    with pytest.raises(ValueError, match="GitHub Actions"):
        admission.validate_invocation(
            admission.CI_CONTEXT,
            admission.SERVICE_ACCOUNT,
            {},
        )

    environment = {
        "GITHUB_ACTIONS": "true",
        "GITHUB_REPOSITORY": admission.CI_REPOSITORY,
        admission.CI_MARKER: "1",
        admission.CI_CONTEXT_MARKER: admission.CI_CONTEXT,
    }
    admission.validate_invocation(
        admission.CI_CONTEXT,
        admission.SERVICE_ACCOUNT,
        environment,
    )


def test_admission_runner_records_the_pinned_kubernetes_client_and_server(
    monkeypatch, capsys,
) -> None:
    calls: list[tuple[str, list[str]]] = []

    def fake_kubectl_json(context, arguments):
        calls.append((context, list(arguments)))
        return {
            "clientVersion": {"gitVersion": admission.KUBERNETES_VERSION},
            "serverVersion": {"gitVersion": admission.KUBERNETES_VERSION},
        }

    monkeypatch.setattr(admission, "kubectl_json", fake_kubectl_json)
    admission.verify_kubernetes_versions(admission.CI_CONTEXT)

    assert calls == [(admission.CI_CONTEXT, ["version"])]
    output = capsys.readouterr().out
    assert f"kubectl client={admission.KUBERNETES_VERSION}" in output
    assert f"API server={admission.KUBERNETES_VERSION}" in output


def test_admission_runner_rejects_an_unpinned_api_server_version(monkeypatch) -> None:
    monkeypatch.setattr(
        admission,
        "kubectl_json",
        lambda _context, _arguments: {
            "clientVersion": {"gitVersion": admission.KUBERNETES_VERSION},
            "serverVersion": {"gitVersion": "v1.35.7"},
        },
    )

    with pytest.raises(RuntimeError, match="must both be v1.35.8"):
        admission.verify_kubernetes_versions(admission.CI_CONTEXT)


def test_admission_runner_accepts_current_generation_type_check_without_conditions(
    monkeypatch, capsys,
) -> None:
    documents = local_admission_documents()
    policy_spec = next(
        item["spec"] for item in documents if item["kind"] == "ValidatingAdmissionPolicy"
    )
    binding_spec = next(
        item["spec"]
        for item in documents
        if item["kind"] == "ValidatingAdmissionPolicyBinding"
    )
    policy = {
        "metadata": {"name": "atlasops-coordinator-rollback-only", "generation": 1},
        "spec": policy_spec,
        "status": {
            "observedGeneration": 1,
            "typeChecking": {"expressionWarnings": []},
        },
    }
    binding = {
        "metadata": {"name": "atlasops-coordinator-rollback-only"},
        "spec": binding_spec,
    }
    calls: list[tuple[str, list[str]]] = []

    def fake_kubectl_json(context, arguments):
        calls.append((context, list(arguments)))
        return policy if arguments[1] == "validatingadmissionpolicy" else binding

    monkeypatch.setattr(admission, "kubectl_json", fake_kubectl_json)
    admission.verify_admission_policy(admission.CI_CONTEXT)

    assert calls == [
        (
            admission.CI_CONTEXT,
            [
                "get",
                "validatingadmissionpolicy",
                "atlasops-coordinator-rollback-only",
            ],
        ),
        (
            admission.CI_CONTEXT,
            [
                "get",
                "validatingadmissionpolicybinding",
                "atlasops-coordinator-rollback-only",
            ],
        ),
    ]
    assert "observedGeneration=1" in capsys.readouterr().out


@pytest.mark.parametrize("status", [
    {"observedGeneration": 0, "typeChecking": {}},
    {"typeChecking": {}},
    {"observedGeneration": 1},
])
def test_admission_runner_rejects_missing_or_stale_type_check_status(
    monkeypatch, status,
) -> None:
    documents = local_admission_documents()
    policy_spec = next(
        item["spec"] for item in documents if item["kind"] == "ValidatingAdmissionPolicy"
    )
    binding_spec = next(
        item["spec"]
        for item in documents
        if item["kind"] == "ValidatingAdmissionPolicyBinding"
    )
    policy = {
        "metadata": {"name": "atlasops-coordinator-rollback-only", "generation": 1},
        "spec": policy_spec,
        "status": {
            "conditions": [{"type": "Accepted", "status": "True"}],
            **status,
        },
    }
    binding = {
        "metadata": {"name": "atlasops-coordinator-rollback-only"},
        "spec": binding_spec,
    }
    monkeypatch.setattr(
        admission,
        "kubectl_json",
        lambda _context, arguments: (
            policy if arguments[1] == "validatingadmissionpolicy" else binding
        ),
    )
    with pytest.raises(RuntimeError, match="type-check status"):
        admission.verify_admission_policy(admission.CI_CONTEXT)


def test_admission_runner_refuses_cel_type_check_warnings(monkeypatch) -> None:
    documents = local_admission_documents()
    policy_spec = next(
        item["spec"] for item in documents if item["kind"] == "ValidatingAdmissionPolicy"
    )
    binding_spec = next(
        item["spec"]
        for item in documents
        if item["kind"] == "ValidatingAdmissionPolicyBinding"
    )
    policy = {
        "metadata": {"name": "atlasops-coordinator-rollback-only", "generation": 1},
        "spec": policy_spec,
        "status": {
            "observedGeneration": 1,
            "typeChecking": {
                "expressionWarnings": [{"fieldRef": "spec.validations[0]"}]
            },
        },
    }
    binding = {
        "metadata": {"name": "atlasops-coordinator-rollback-only"},
        "spec": binding_spec,
    }

    monkeypatch.setattr(
        admission,
        "kubectl_json",
        lambda _context, arguments: (
            policy if arguments[1] == "validatingadmissionpolicy" else binding
        ),
    )

    with pytest.raises(RuntimeError, match="warnings"):
        admission.verify_admission_policy(admission.CI_CONTEXT)


def test_admission_runner_refuses_an_ambient_context_or_different_identity() -> None:
    with pytest.raises(ValueError, match="exact AtlasOps coordinator"):
        admission.validate_invocation(
            admission.CI_CONTEXT,
            "system:serviceaccount:default:default",
            {
                "GITHUB_ACTIONS": "true",
                "GITHUB_REPOSITORY": admission.CI_REPOSITORY,
                admission.CI_MARKER: "1",
                admission.CI_CONTEXT_MARKER: admission.CI_CONTEXT,
            },
        )

    with pytest.raises(SystemExit) as error:
        admission.build_parser().parse_args(
            ["--service-account", admission.SERVICE_ACCOUNT]
        )
    assert error.value.code == 2


def test_admission_runner_checks_cluster_ownership_before_any_fixture(monkeypatch) -> None:
    def reject_owner(_cluster_name, _context):
        raise RuntimeError("disposable Kind node ownership is unverified")

    def unsafe_progress(_context):
        raise AssertionError("admission progressed before independently checking ownership")

    monkeypatch.setattr(platform_verify, "verify_existing_kind_cluster", reject_owner)
    monkeypatch.setattr(admission, "verify_kubernetes_versions", unsafe_progress)

    with pytest.raises(RuntimeError, match="Kind node ownership"):
        admission.verify_disposable_cluster(
            admission.CI_CONTEXT, admission.SERVICE_ACCOUNT
        )


def test_rollback_dry_run_uses_the_existing_exact_json_patch_shape() -> None:
    application = admission.application_seed()
    application["metadata"].update({"uid": "fixture-uid", "resourceVersion": "17"})

    patch = admission.rollback_patch(application)

    assert patch[:3] == [
        {"op": "test", "path": "/metadata/uid", "value": "fixture-uid"},
        {"op": "test", "path": "/metadata/resourceVersion", "value": "17"},
        {
            "op": "test",
            "path": "/spec/source",
            "value": application["spec"]["source"],
        },
    ]
    assert patch[3]["op"] == "replace"
    assert patch[3]["path"] == "/spec/source"
    assert patch[3]["value"]["repoURL"] == admission.APPLICATION_REPOSITORY
    assert patch[3]["value"]["path"] == admission.APPLICATION_PATH
    assert patch[3]["value"]["targetRevision"] == admission.ROLLBACK_REVISION
    assert patch[4] == {
        "op": "add",
        "path": "/operation",
        "value": admission.rollback_operation(admission.ROLLBACK_REVISION),
    }


def test_admission_dry_run_matrix_covers_approved_denial_boundaries() -> None:
    application = admission.application_seed()
    application["metadata"].update({"uid": "fixture-uid", "resourceVersion": "17"})
    required_denials = {
        "repository",
        "path",
        "project",
        "destination",
        "sync policy",
        "tracking metadata",
        "multiple sources",
        "pruning",
        "initiator",
        "unrecorded revision",
        "extra operation fields",
        "status mutation",
    }

    cases = dict(admission.rejected_update_cases(application))

    assert required_denials <= set(cases)
    assert admission.rollback_patch(application, admission.UNRECORDED_REVISION)[3][
        "value"
    ]["targetRevision"] == admission.UNRECORDED_REVISION


def test_ci_admission_runner_checks_the_pinned_argo_chart_on_explicit_context(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(
            command,
            0,
            '[{"name":"argocd","chart":"argo-cd-10.3.2"}]',
            "",
        )

    monkeypatch.setattr(admission.subprocess, "run", fake_run)
    admission.verify_argo_chart(admission.CI_CONTEXT)

    assert commands == [
        [
            "helm",
            f"--kube-context={admission.CI_CONTEXT}",
            "list",
            "--namespace",
            admission.APPLICATION_NAMESPACE,
            "--output",
            "json",
        ]
    ]


def test_all_runner_kubectl_calls_include_the_selected_context_and_identity(
    monkeypatch,
) -> None:
    commands: list[list[str]] = []

    def fake_run(command, **_kwargs):
        commands.append(list(command))
        return subprocess.CompletedProcess(command, 0, "{}", "")

    monkeypatch.setattr(admission.subprocess, "run", fake_run)
    admission.run_kubectl(
        admission.CI_CONTEXT,
        ["get", "application", "atlasops-boutique"],
        as_identity=admission.SERVICE_ACCOUNT,
    )

    assert commands == [
        [
            "kubectl",
            f"--context={admission.CI_CONTEXT}",
            f"--as={admission.SERVICE_ACCOUNT}",
            "get",
            "application",
            "atlasops-boutique",
        ]
    ]


def test_admission_contract_has_a_keyless_disposable_ci_job() -> None:
    root = Path(admission.__file__).resolve().parents[1]
    workflow_path = root / ".github" / "workflows" / "platform-admission.yml"
    assert workflow_path.is_file(), "The real admission matrix requires its dedicated CI job"
    workflow = admission.yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    assert workflow["permissions"] == {"contents": "read"}
    job = workflow["jobs"]["admission"]
    assert "head.repo.full_name == github.repository" in job["if"]
    assert job["env"][admission.CI_MARKER] == "1"
    assert job["env"][admission.CI_CONTEXT_MARKER] == admission.CI_CONTEXT
    assert not any("KEY" in name or "TOKEN" in name for name in job["env"])
    commands = "\n".join(step.get("run", "") for step in job["steps"])
    assert "python -B -m scripts.verify_platform_admission" in commands
    assert "inspect-kind-cluster" in commands
    assert "kind delete cluster" in commands
    assert f"--version {admission.ARGO_CHART.removeprefix('argo-cd-')}" in commands
    cleanup = next(step for step in job["steps"] if step["name"] == "Remove the owned CI cluster")
    assert cleanup["if"] == "always() && steps.cluster.outcome == 'success'"
