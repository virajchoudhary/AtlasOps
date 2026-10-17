from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from agents.tools import kubectl


@pytest.fixture
def subprocess_run(monkeypatch):
    monkeypatch.delenv("KUBECONFIG_CONTEXT", raising=False)
    run = Mock(
        return_value=SimpleNamespace(returncode=0, stdout="scaled", stderr="")
    )
    monkeypatch.setattr(kubectl.subprocess, "run", run)
    return run


def assert_rejected_without_dispatch(result, subprocess_run):
    assert result["success"] is False
    assert isinstance(result.get("error"), str)
    assert result["error"]
    subprocess_run.assert_not_called()


@pytest.mark.parametrize(
    "deployment",
    [
        "--all",
        "-A",
        "pod/frontend",
        "service/frontend",
        "deployment/frontend",
        "../frontend",
        "frontend/child",
        "frontend --all",
        "frontend;kubectl",
        " Frontend",
        "frontend\n",
        "",
        "a" * 254,
        ".".join(["a" * 63, "b" * 63, "c" * 63, "d" * 62]),
        None,
        123,
    ],
)
def test_rejects_non_bare_or_invalid_deployment_names(deployment, subprocess_run):
    result = kubectl.kubectl_scale(deployment, 1)

    assert_rejected_without_dispatch(result, subprocess_run)


@pytest.mark.parametrize(
    "namespace",
    [
        "--all",
        "-A",
        "--namespace=other",
        "default;kubectl",
        "prod/child",
        "default namespace",
        "default\n",
        "",
        "n" * 64,
        None,
        123,
    ],
)
def test_rejects_invalid_namespace_without_dispatch(namespace, subprocess_run):
    result = kubectl.kubectl_scale("frontend", 1, namespace=namespace)

    assert_rejected_without_dispatch(result, subprocess_run)


@pytest.mark.parametrize("replicas", [True, False, "3", 3.0, None])
def test_rejects_non_integer_replica_values_without_dispatch(
    replicas, subprocess_run
):
    result = kubectl.kubectl_scale("frontend", replicas)

    assert_rejected_without_dispatch(result, subprocess_run)


@pytest.mark.parametrize("replicas", [-1, 21])
def test_rejects_replica_values_outside_range_without_dispatch(
    replicas, subprocess_run
):
    result = kubectl.kubectl_scale("frontend", replicas)

    assert_rejected_without_dispatch(result, subprocess_run)


def test_uses_exact_single_deployment_argv(subprocess_run):
    result = kubectl.kubectl_scale("frontend", 3)

    assert result["success"] is True
    subprocess_run.assert_called_once_with(
        [
            "kubectl",
            "scale",
            "deployment",
            "frontend",
            "--replicas=3",
            "-n",
            "default",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )


@pytest.mark.parametrize(
    ("deployment", "replicas", "namespace"),
    [
        ("payments.api", 0, "n" * 63),
        ("a" * 253, 20, "production"),
        ("a" * 63 + "." + "b" * 64, 1, "default"),
        (
            ".".join(
                ["a" * 63, "b" * 63, "c" * 63, "d" * 61]
            ),
            20,
            "production",
        ),
    ],
)
def test_accepts_valid_kubernetes_name_and_replica_boundaries(
    deployment, replicas, namespace, subprocess_run
):
    result = kubectl.kubectl_scale(deployment, replicas, namespace=namespace)

    assert result["success"] is True
    command = subprocess_run.call_args.args[0]
    assert command == [
        "kubectl",
        "scale",
        "deployment",
        deployment,
        f"--replicas={replicas}",
        "-n",
        namespace,
    ]
