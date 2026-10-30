from pathlib import Path

import pytest
import yaml

from agents.tools.chaos import ALLOWED_CHAOS_KINDS, ALLOWED_CHAOS_NAMESPACES

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture(
    params=[
        "infra/local/coordinator-local.yaml",
        "infra/kubernetes/coordinator.yaml.tmpl",
    ],
    ids=["kind", "gke"],
)
def documents(request):
    return [
        document
        for document in yaml.safe_load_all(
            (ROOT / request.param).read_text(encoding="utf-8")
        )
        if document
    ]


def test_chaos_inspection_matches_the_six_supported_kinds(documents, request):
    if request.node.callspec.id == "kind":
        chaos_role = next(
            document
            for document in documents
            if document["kind"] == "Role"
            and document["metadata"]["name"] == "atlasops-coordinator-chaos"
        )
        assert chaos_role["metadata"]["namespace"] == "chaos-mesh"
        expected_verbs = {"get", "list", "delete"}
    else:
        chaos_role = next(
            document
            for document in documents
            if document["kind"] == "ClusterRole"
            and document["metadata"]["name"] == "atlasops-coordinator-readonly"
        )
        expected_verbs = {"get", "list", "watch"}
    chaos_rules = [
        rule
        for rule in chaos_role["rules"]
        if rule["apiGroups"] == ["chaos-mesh.org"]
    ]

    assert len(chaos_rules) == 1
    assert set(chaos_rules[0]["resources"]) == ALLOWED_CHAOS_KINDS
    assert set(chaos_rules[0]["verbs"]) == expected_verbs


def test_chaos_cleanup_is_namespace_scoped_without_fault_creation(documents):
    cleanup_roles = [
        document
        for document in documents
        if document["kind"] == "Role"
        and document["metadata"]["name"] == "atlasops-coordinator-chaos-cleanup"
        and any(
            rule["apiGroups"] == ["chaos-mesh.org"]
            for rule in document["rules"]
        )
    ]

    assert {role["metadata"]["namespace"] for role in cleanup_roles} == (
        ALLOWED_CHAOS_NAMESPACES
    )
    for role in cleanup_roles:
        assert len(role["rules"]) == 1
        rule = role["rules"][0]
        assert set(rule["resources"]) == ALLOWED_CHAOS_KINDS
        assert set(rule["verbs"]) == {"get", "delete"}
        binding = next(
            document
            for document in documents
            if document["kind"] == "RoleBinding"
            and document["metadata"]["namespace"] == role["metadata"]["namespace"]
            and document["roleRef"]["name"] == role["metadata"]["name"]
        )
        assert binding["roleRef"]["kind"] == "Role"
        assert binding["subjects"] == [
            {
                "kind": "ServiceAccount",
                "name": "atlasops-coordinator",
                "namespace": "default",
            }
        ]


def test_chaos_write_permissions_never_escape_the_cleanup_role(documents):
    for document in documents:
        if document["kind"] not in {"Role", "ClusterRole"}:
            continue
        for rule in document["rules"]:
            assert "*" not in rule["apiGroups"]
            assert "*" not in rule["resources"]
            assert "*" not in rule["verbs"]
            assert "secrets" not in rule["resources"]
            if "chaos-mesh.org" not in rule["apiGroups"]:
                continue
            assert not {"create", "patch", "update", "deletecollection"} & set(
                rule["verbs"]
            )
            if "delete" in rule["verbs"]:
                assert document["kind"] == "Role"
                assert document["metadata"]["namespace"] in ALLOWED_CHAOS_NAMESPACES


def test_gke_coordinator_can_start_when_argo_is_explicitly_disabled():
    documents = [
        document
        for document in yaml.safe_load_all(
            (ROOT / "infra/kubernetes/coordinator.yaml.tmpl").read_text(
                encoding="utf-8"
            )
        )
        if document
    ]
    deployment = next(
        document for document in documents if document["kind"] == "Deployment"
    )
    env = deployment["spec"]["template"]["spec"]["containers"][0]["env"]
    argo_keys = [
        item["valueFrom"]["secretKeyRef"]
        for item in env
        if item["name"] in {"ARGOCD_USER", "ARGOCD_PASS"}
    ]
    assert len(argo_keys) == 2
    assert all(reference.get("optional") is True for reference in argo_keys)
