#!/usr/bin/env python3
"""Exercise local Argo admission and coordinator RBAC in disposable GitHub CI."""

from __future__ import annotations

import argparse
import copy
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

import yaml

from scripts import verify_platform_infrastructure as platform_verify

CI_REPOSITORY = "virajchoudhary/AtlasOps"
CI_CONTEXT = "kind-atlasops-gitops-admission-ci"
CI_MARKER = "ATLASOPS_DISPOSABLE_ADMISSION_TEST"
CI_CONTEXT_MARKER = "ATLASOPS_DISPOSABLE_ADMISSION_CONTEXT"
ARGO_RELEASE_NAME = "argocd"
ARGO_CHART = "argo-cd-10.3.2"
KUBERNETES_VERSION = "v1.35.8"
SERVICE_ACCOUNT = "system:serviceaccount:default:atlasops-coordinator"
APPLICATION_NAME = "atlasops-boutique"
APPLICATION_NAMESPACE = "argocd"
APPLICATION_PROJECT = "atlasops-boutique"
APPLICATION_REPOSITORY = "https://github.com/virajchoudhary/AtlasOps.git"
APPLICATION_PATH = "infra/local/boutique"
BOUTIQUE_NAMESPACE = "atlasops-boutique"
ROLLBACK_REVISION = "b" * 40
CURRENT_REVISION = "a" * 40
UNRECORDED_REVISION = "c" * 40
POLICY_DENIAL_FRAGMENT = "AtlasOps coordinator may only request an exact non-pruning rollback"
PROBE_DEPLOYMENT = "frontend"


def validate_invocation(
    context: str,
    service_account: str,
    environ: Mapping[str, str] | None = None,
) -> None:
    environment = os.environ if environ is None else environ
    if context != CI_CONTEXT:
        raise ValueError(f"only the disposable context {CI_CONTEXT!r} is supported")
    if service_account != SERVICE_ACCOUNT:
        raise ValueError("the exact AtlasOps coordinator service account is required")
    if (
        environment.get("GITHUB_ACTIONS") != "true"
        or environment.get("GITHUB_REPOSITORY") != CI_REPOSITORY
        or environment.get(CI_MARKER) != "1"
        or environment.get(CI_CONTEXT_MARKER) != context
    ):
        raise ValueError(
            "this runner is restricted to the explicitly marked disposable "
            "AtlasOps GitHub Actions job"
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--context", required=True)
    parser.add_argument("--service-account", required=True)
    return parser


def run_kubectl(
    context: str,
    arguments: Sequence[str],
    *,
    as_identity: str | None = None,
    input_text: str | None = None,
) -> subprocess.CompletedProcess[str]:
    command = ["kubectl", f"--context={context}"]
    if as_identity is not None:
        command.append(f"--as={as_identity}")
    command.extend(arguments)
    return subprocess.run(
        command,
        input=input_text,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )


def require_success(
    result: subprocess.CompletedProcess[str], description: str
) -> str:
    if result.returncode != 0:
        details = (result.stdout + "\n" + result.stderr).strip()
        raise RuntimeError(
            f"{description} failed with exit code {result.returncode}: "
            f"{details[-1200:]}"
        )
    return result.stdout


def kubectl_json(
    context: str,
    arguments: Sequence[str],
    *,
    as_identity: str | None = None,
) -> dict[str, Any]:
    output = require_success(
        run_kubectl(
            context,
            [*arguments, "-o", "json"],
            as_identity=as_identity,
        ),
        "kubectl JSON read",
    )
    try:
        value = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("kubectl did not return valid JSON") from exc
    if not isinstance(value, dict):
        raise RuntimeError("kubectl did not return a JSON object")
    return value


def require_namespace(context: str, namespace: str) -> None:
    require_success(
        run_kubectl(context, ["get", "namespace", namespace]),
        f"required namespace {namespace}",
    )


def require_missing_application(context: str) -> None:
    result = run_kubectl(
        context,
        [
            "get",
            "application",
            APPLICATION_NAME,
            "--namespace",
            APPLICATION_NAMESPACE,
            "-o",
            "json",
        ],
    )
    output = result.stdout + result.stderr
    if result.returncode == 0 or "NotFound" not in output:
        raise RuntimeError(
            "the disposable cluster must not already contain "
            f"{APPLICATION_NAMESPACE}/{APPLICATION_NAME}"
        )


def verify_application_crd(context: str) -> None:
    crd = kubectl_json(context, ["get", "crd", "applications.argoproj.io"])
    versions = crd.get("spec", {}).get("versions")
    if not isinstance(versions, list):
        raise RuntimeError("Argo Application CRD has no version schemas")
    version = next(
        (
            item
            for item in versions
            if isinstance(item, dict) and item.get("name") == "v1alpha1"
        ),
        None,
    )
    properties = (
        version.get("schema", {})
        .get("openAPIV3Schema", {})
        .get("properties", {})
        if isinstance(version, dict)
        else {}
    )
    spec_properties = properties.get("spec", {}).get("properties", {})
    operation_properties = properties.get("operation", {}).get("properties", {})
    operation_sync_properties = operation_properties.get("sync", {}).get(
        "properties", {}
    )
    status_properties = properties.get("status", {}).get("properties", {})
    history_properties = (
        status_properties.get("history", {}).get("items", {}).get("properties", {})
    )
    if not {
        "destination",
        "project",
        "source",
        "sources",
        "syncPolicy",
    } <= set(spec_properties):
        raise RuntimeError("installed Argo Application CRD spec schema is incomplete")
    if not {"initiatedBy", "sync"} <= set(operation_properties):
        raise RuntimeError("installed Argo Application CRD operation schema is incomplete")
    if not {"prune", "revision"} <= set(operation_sync_properties):
        raise RuntimeError("installed Argo Application CRD sync schema is incomplete")
    if not {"history"} <= set(status_properties) or not {
        "revision",
        "source",
        "sources",
    } <= set(history_properties):
        raise RuntimeError("installed Argo Application CRD history schema is incomplete")
    print("Installed Application CRD exposes the pinned rollback fields.")


def verify_kubernetes_versions(context: str) -> None:
    version_info = kubectl_json(context, ["version"])
    client_version = (version_info.get("clientVersion") or {}).get("gitVersion")
    server_version = (version_info.get("serverVersion") or {}).get("gitVersion")
    if client_version != KUBERNETES_VERSION or server_version != KUBERNETES_VERSION:
        raise RuntimeError(
            f"disposable Kubernetes client and server must both be "
            f"{KUBERNETES_VERSION}; got client={client_version!r}, "
            f"server={server_version!r}"
        )
    print(
        f"Kubernetes kubectl client={client_version}; "
        f"API server={server_version}."
    )


def verify_admission_policy(context: str) -> None:
    manifest_path = (
        Path(__file__).resolve().parents[1]
        / "infra"
        / "local"
        / "operational-admission.yaml"
    )
    try:
        manifest_documents = [
            item
            for item in yaml.safe_load_all(
                manifest_path.read_text(encoding="utf-8")
            )
            if isinstance(item, dict)
        ]
    except (OSError, yaml.YAMLError) as exc:
        raise RuntimeError("local coordinator admission manifest is unreadable") from exc
    expected_policies = [
        item
        for item in manifest_documents
        if item.get("kind") == "ValidatingAdmissionPolicy"
        and item.get("metadata", {}).get("name") == "atlasops-coordinator-rollback-only"
    ]
    expected_bindings = [
        item
        for item in manifest_documents
        if item.get("kind") == "ValidatingAdmissionPolicyBinding"
        and item.get("metadata", {}).get("name") == "atlasops-coordinator-rollback-only"
    ]
    if len(expected_policies) != 1 or len(expected_bindings) != 1:
        raise RuntimeError("local coordinator admission manifest is incomplete")

    policy = kubectl_json(
        context,
        ["get", "validatingadmissionpolicy", "atlasops-coordinator-rollback-only"],
    )
    binding = kubectl_json(
        context,
        [
            "get",
            "validatingadmissionpolicybinding",
            "atlasops-coordinator-rollback-only",
        ],
    )
    expected_spec = expected_policies[0].get("spec", {})
    actual_spec = policy.get("spec", {})
    if (
        policy.get("metadata", {}).get("name") != "atlasops-coordinator-rollback-only"
        or actual_spec.get("failurePolicy") != "Fail"
        or actual_spec.get("matchConstraints", {}).get("resourceRules")
        != expected_spec.get("matchConstraints", {}).get("resourceRules")
        or actual_spec.get("matchConditions") != expected_spec.get("matchConditions")
        or actual_spec.get("validations") != expected_spec.get("validations")
    ):
        raise RuntimeError(
            "installed coordinator admission policy does not match the local contract"
        )

    try:
        platform_verify.validate_admission_policy_status(policy)
    except ValueError as exc:
        raise RuntimeError(
            f"coordinator admission policy type-check status is invalid: {exc}"
        ) from exc

    binding_spec = binding.get("spec", {})
    expected_binding_spec = expected_bindings[0].get("spec", {})
    if (
        binding.get("metadata", {}).get("name") != "atlasops-coordinator-rollback-only"
        or binding_spec.get("policyName") != expected_binding_spec.get("policyName")
        or binding_spec.get("validationActions")
        != expected_binding_spec.get("validationActions")
    ):
        raise RuntimeError("coordinator admission policy binding does not enforce Deny")
    print(
        f"Admission policy observedGeneration={policy['status']['observedGeneration']}; "
        "expressionWarnings=0; "
        "binding validationActions=Deny."
    )


def verify_argo_chart(context: str) -> None:
    result = subprocess.run(
        [
            "helm",
            f"--kube-context={context}",
            "list",
            "--namespace",
            APPLICATION_NAMESPACE,
            "--output",
            "json",
        ],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    output = require_success(result, "explicit-context Argo Helm release read")
    try:
        releases = json.loads(output)
    except json.JSONDecodeError as exc:
        raise RuntimeError("Helm did not return valid JSON release metadata") from exc
    if not isinstance(releases, list):
        raise RuntimeError("Helm did not return a release list")
    matching = [
        release
        for release in releases
        if isinstance(release, dict) and release.get("name") == ARGO_RELEASE_NAME
    ]
    if len(matching) != 1 or matching[0].get("chart") != ARGO_CHART:
        raise RuntimeError(f"disposable cluster must run Helm chart {ARGO_CHART}")
    print(f"Argo Helm release is pinned to {ARGO_CHART}.")


def application_seed() -> dict[str, Any]:
    return {
        "apiVersion": "argoproj.io/v1alpha1",
        "kind": "Application",
        "metadata": {
            "name": APPLICATION_NAME,
            "namespace": APPLICATION_NAMESPACE,
            "labels": {
                "argocd.argoproj.io/instance": APPLICATION_NAME,
                "app.kubernetes.io/part-of": "argocd",
            },
            "annotations": {
                "argocd.argoproj.io/tracking-id": (
                    f"{APPLICATION_NAME}:argoproj.io/Application:"
                    f"{APPLICATION_NAMESPACE}/{APPLICATION_NAME}"
                )
            },
        },
        "spec": {
            "project": APPLICATION_PROJECT,
            "source": {
                "repoURL": APPLICATION_REPOSITORY,
                "targetRevision": CURRENT_REVISION,
                "path": APPLICATION_PATH,
            },
            "destination": {
                "server": "https://kubernetes.default.svc",
                "namespace": BOUTIQUE_NAMESPACE,
            },
            "syncPolicy": {
                "automated": {
                    "enabled": True,
                    "prune": False,
                    "selfHeal": False,
                    "allowEmpty": False,
                },
                "syncOptions": [],
            },
        },
        "status": {
            "history": [
                {
                    "deployedAt": "2026-01-01T00:00:00Z",
                    "id": 1,
                    "revision": ROLLBACK_REVISION,
                    "source": {
                        "repoURL": APPLICATION_REPOSITORY,
                        "path": APPLICATION_PATH,
                    },
                }
            ],
            "sync": {"status": "Synced", "revision": CURRENT_REVISION},
        },
    }


def deployment_seed() -> dict[str, Any]:
    labels = {"app": PROBE_DEPLOYMENT}
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": PROBE_DEPLOYMENT, "namespace": BOUTIQUE_NAMESPACE},
        "spec": {
            "replicas": 0,
            "selector": {"matchLabels": labels},
            "template": {
                "metadata": {"labels": labels},
                "spec": {
                    "containers": [
                        {
                            "name": "pause",
                            "image": "registry.k8s.io/pause:3.10",
                        }
                    ]
                },
            },
        },
    }


def create_fixture(context: str, resource: dict[str, Any], description: str) -> None:
    output = yaml.safe_dump(resource, sort_keys=False)
    require_success(
        run_kubectl(context, ["create", "-f", "-"], input_text=output),
        f"create disposable {description} fixture",
    )


def get_application(context: str) -> dict[str, Any]:
    return kubectl_json(
        context,
        [
            "get",
            "application",
            APPLICATION_NAME,
            "--namespace",
            APPLICATION_NAMESPACE,
        ],
    )


def rollback_operation(revision: str) -> dict[str, Any]:
    return {
        "initiatedBy": {"username": "atlasops"},
        "sync": {"revision": revision, "prune": False},
    }


def rollback_patch(
    application: dict[str, Any],
    revision: str = ROLLBACK_REVISION,
    *,
    source_overrides: Mapping[str, Any] | None = None,
    operation: Mapping[str, Any] | None = None,
    additional_operations: Sequence[dict[str, Any]] = (),
) -> list[dict[str, Any]]:
    metadata = application["metadata"]
    old_source = copy.deepcopy(application["spec"]["source"])
    new_source = copy.deepcopy(old_source)
    new_source["targetRevision"] = revision
    if source_overrides:
        new_source.update(source_overrides)
    patch: list[dict[str, Any]] = [
        {"op": "test", "path": "/metadata/uid", "value": metadata["uid"]},
        {
            "op": "test",
            "path": "/metadata/resourceVersion",
            "value": metadata["resourceVersion"],
        },
        {"op": "test", "path": "/spec/source", "value": old_source},
        {"op": "replace", "path": "/spec/source", "value": new_source},
        {
            "op": "add",
            "path": "/operation",
            "value": copy.deepcopy(operation or rollback_operation(revision)),
        },
    ]
    patch.extend(copy.deepcopy(list(additional_operations)))
    return patch


def patch_dry_run(
    context: str, service_account: str, patch: Sequence[dict[str, Any]]
) -> subprocess.CompletedProcess[str]:
    return run_kubectl(
        context,
        [
            "patch",
            "application",
            APPLICATION_NAME,
            "--namespace",
            APPLICATION_NAMESPACE,
            "--type=json",
            "--patch",
            json.dumps(list(patch), separators=(",", ":")),
            "--dry-run=server",
            "-o",
            "json",
        ],
        as_identity=service_account,
    )


def require_policy_denial(
    context: str,
    service_account: str,
    application: dict[str, Any],
    label: str,
    **patch_options: Any,
) -> None:
    result = patch_dry_run(
        context,
        service_account,
        rollback_patch(application, **patch_options),
    )
    output = result.stdout + "\n" + result.stderr
    if result.returncode == 0 or POLICY_DENIAL_FRAGMENT not in output:
        raise RuntimeError(
            f"invalid Application update {label!r} was not rejected by the "
            "coordinator ValidatingAdmissionPolicy"
        )


def rejected_update_cases(
    application: dict[str, Any],
) -> list[tuple[str, dict[str, Any]]]:
    return [
        (
            "repository",
            {
                "source_overrides": {
                    "repoURL": "https://example.invalid/not-owned.git"
                }
            },
        ),
        (
            "path",
            {"source_overrides": {"path": "not-the-owned/path"}},
        ),
        (
            "project",
            {
                "additional_operations": [
                    {"op": "replace", "path": "/spec/project", "value": "default"}
                ]
            },
        ),
        (
            "destination",
            {
                "additional_operations": [
                    {
                        "op": "replace",
                        "path": "/spec/destination/namespace",
                        "value": "default",
                    }
                ]
            },
        ),
        (
            "sync policy",
            {
                "additional_operations": [
                    {
                        "op": "replace",
                        "path": "/spec/syncPolicy/automated/prune",
                        "value": True,
                    }
                ]
            },
        ),
        (
            "tracking metadata",
            {
                "additional_operations": [
                    {
                        "op": "replace",
                        "path": (
                            "/metadata/annotations/"
                            "argocd.argoproj.io~1tracking-id"
                        ),
                        "value": "not-the-observed-tracking-id",
                    }
                ]
            },
        ),
        (
            "multiple sources",
            {
                "additional_operations": [
                    {
                        "op": "add",
                        "path": "/spec/sources",
                        "value": [
                            copy.deepcopy(application["spec"]["source"]),
                            copy.deepcopy(application["spec"]["source"]),
                        ],
                    }
                ]
            },
        ),
        (
            "pruning",
            {
                "operation": {
                    "initiatedBy": {"username": "atlasops"},
                    "sync": {
                        "revision": ROLLBACK_REVISION,
                        "prune": True,
                    },
                }
            },
        ),
        (
            "initiator",
            {
                "operation": {
                    "initiatedBy": {"username": "another-user"},
                    "sync": {
                        "revision": ROLLBACK_REVISION,
                        "prune": False,
                    },
                }
            },
        ),
        (
            "unrecorded revision",
            {"revision": UNRECORDED_REVISION},
        ),
        (
            "extra operation fields",
            {
                "operation": {
                    **rollback_operation(ROLLBACK_REVISION),
                    "info": [{"name": "unexpected", "value": "field"}],
                }
            },
        ),
        (
            "status mutation",
            {
                "additional_operations": [
                    {
                        "op": "replace",
                        "path": "/status/sync/revision",
                        "value": UNRECORDED_REVISION,
                    }
                ]
            },
        ),
    ]


def assert_idle_operation_is_required(
    context: str, service_account: str, application: dict[str, Any]
) -> dict[str, Any]:
    active_operation = {
        "initiatedBy": {"username": "someone-else"},
        "sync": {"revision": CURRENT_REVISION, "prune": False},
    }
    require_success(
        run_kubectl(
            context,
            [
                "patch",
                "application",
                APPLICATION_NAME,
                "--namespace",
                APPLICATION_NAMESPACE,
                "--type=merge",
                "--patch",
                json.dumps({"operation": active_operation}, separators=(",", ":")),
                "-o",
                "json",
            ],
        ),
        "seed a non-idle old Application operation",
    )
    active_application = get_application(context)
    try:
        require_policy_denial(
            context,
            service_account,
            active_application,
            "non-idle old operation",
        )
    finally:
        require_success(
            run_kubectl(
                context,
                [
                    "patch",
                    "application",
                    APPLICATION_NAME,
                    "--namespace",
                    APPLICATION_NAMESPACE,
                    "--type=json",
                    "--patch",
                    '[{"op":"remove","path":"/operation"}]',
                ],
            ),
            "restore the disposable Application's idle state",
        )
    return get_application(context)


def assert_read_only_workload_access(
    context: str, service_account: str
) -> None:
    get_result = run_kubectl(
        context,
        [
            "get",
            "deployment",
            PROBE_DEPLOYMENT,
            "--namespace",
            BOUTIQUE_NAMESPACE,
            "-o",
            "json",
        ],
        as_identity=service_account,
    )
    require_success(get_result, "coordinator Deployment read")
    list_result = run_kubectl(
        context,
        ["get", "replicasets", "--namespace", BOUTIQUE_NAMESPACE, "-o", "json"],
        as_identity=service_account,
    )
    require_success(list_result, "coordinator ReplicaSet read")

    patch_result = run_kubectl(
        context,
        [
            "patch",
            "deployment",
            PROBE_DEPLOYMENT,
            "--namespace",
            BOUTIQUE_NAMESPACE,
            "--type=merge",
            "--patch",
            '{"spec":{"replicas":1}}',
            "--dry-run=server",
        ],
        as_identity=service_account,
    )
    if patch_result.returncode == 0 or "forbidden" not in (
        patch_result.stdout + patch_result.stderr
    ).casefold():
        raise RuntimeError("coordinator Deployment patch was not denied by RBAC")

    deployment = deployment_seed()
    update_result = run_kubectl(
        context,
        [
            "replace",
            "--namespace",
            BOUTIQUE_NAMESPACE,
            "--dry-run=server",
            "-f",
            "-",
        ],
        as_identity=service_account,
        input_text=yaml.safe_dump(deployment, sort_keys=False),
    )
    if update_result.returncode == 0 or "forbidden" not in (
        update_result.stdout + update_result.stderr
    ).casefold():
        raise RuntimeError("coordinator Deployment update was not denied by RBAC")

    scale_result = run_kubectl(
        context,
        [
            "scale",
            f"deployment/{PROBE_DEPLOYMENT}",
            "--namespace",
            BOUTIQUE_NAMESPACE,
            "--replicas=1",
            "--dry-run=server",
        ],
        as_identity=service_account,
    )
    if scale_result.returncode == 0 or "forbidden" not in (
        scale_result.stdout + scale_result.stderr
    ).casefold():
        raise RuntimeError("coordinator Deployment scale was not denied by RBAC")
    print("Coordinator Deployment and ReplicaSet reads work; patch, update, and scale are denied.")


def verify_disposable_cluster(context: str, service_account: str) -> None:
    if not platform_verify.verify_existing_kind_cluster(
        context.removeprefix("kind-"), context
    ):
        raise RuntimeError("disposable Kind node ownership is unverified")
    verify_kubernetes_versions(context)
    verify_argo_chart(context)
    verify_application_crd(context)
    verify_admission_policy(context)
    require_namespace(context, APPLICATION_NAMESPACE)
    require_namespace(context, BOUTIQUE_NAMESPACE)
    require_missing_application(context)
    create_fixture(context, application_seed(), "Application")
    create_fixture(context, deployment_seed(), "Deployment")
    application = get_application(context)
    allowed = patch_dry_run(
        context,
        service_account,
        rollback_patch(application),
    )
    allowed_json = require_success(allowed, "recorded rollback dry run")
    try:
        allowed_application = json.loads(allowed_json)
    except json.JSONDecodeError as exc:
        raise RuntimeError("allowed dry-run response was not valid JSON") from exc
    if (
        allowed_application.get("spec", {}).get("source", {}).get("targetRevision")
        != ROLLBACK_REVISION
        or allowed_application.get("operation") != rollback_operation(ROLLBACK_REVISION)
        or allowed_application.get("status") != application.get("status")
        or allowed_application.get("metadata", {}).get("labels")
        != application.get("metadata", {}).get("labels")
        or allowed_application.get("metadata", {}).get("annotations")
        != application.get("metadata", {}).get("annotations")
    ):
        raise RuntimeError("allowed rollback dry run did not preserve the expected object state")
    print("Recorded, same-source, non-pruning rollback is admitted by server-side dry run.")

    application = assert_idle_operation_is_required(
        context, service_account, application
    )
    for label, options in rejected_update_cases(application):
        require_policy_denial(context, service_account, application, label, **options)
        print(f"Rejected unsafe Application update: {label}.")

    assert_read_only_workload_access(context, service_account)


def main() -> int:
    args = build_parser().parse_args()
    try:
        validate_invocation(args.context, args.service_account)
        verify_disposable_cluster(args.context, args.service_account)
    except (OSError, RuntimeError, TypeError, ValueError) as exc:
        print(f"PLATFORM ADMISSION CHECK FAILED: {exc}", file=sys.stderr)
        return 1
    print(
        "PLATFORM ADMISSION CHECK PASSED: server-side dry-run evidence from "
        f"{args.context}; no Application or Deployment mutation was persisted "
        "by the coordinator identity."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
