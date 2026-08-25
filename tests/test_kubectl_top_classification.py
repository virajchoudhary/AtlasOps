"""Deterministic classification for `kubectl top` Metrics API dependency."""

from unittest.mock import patch

from agents.tools.kubectl import kubectl_top_nodes, kubectl_top_pods


def _run_result(stdout="", stderr="", returncode=0):
    return {"success": returncode == 0, "stdout": stdout, "stderr": stderr, "returncode": returncode}


def test_missing_metrics_api_is_classified():
    failing = _run_result(stderr="error: Metrics API not available\n", returncode=1)
    with patch("agents.tools.kubectl._run", return_value=failing):
        result = kubectl_top_pods(namespace="default")
    assert result["success"] is False
    assert result["error_class"] == "metrics_api_unavailable"
    assert result["error"] == "metrics-server Metrics API is not available in this cluster"
    # Original raw observation is preserved for evidence.
    assert result["stderr"] == "error: Metrics API not available\n"
    assert result["returncode"] == 1


def test_top_nodes_same_dependency_classification():
    failing = _run_result(stderr="error: Metrics API not available\n", returncode=1)
    with patch("agents.tools.kubectl._run", return_value=failing):
        result = kubectl_top_nodes()
    assert result["error_class"] == "metrics_api_unavailable"


def test_healthy_top_output_passes_through_untouched():
    good = _run_result(stdout='NAME              CPU(cores)   MEMORY(bytes)\npaymentservice…   1m           22Mi\n')
    with patch("agents.tools.kubectl._run", return_value=good) as run:
        result = kubectl_top_pods(namespace="default")
    assert result["success"] is True
    assert "error_class" not in result
    run.assert_called_once_with(["kubectl", "top", "pods", "-n", "default"])


def test_unrelated_failure_is_not_misclassified():
    unrelated = _run_result(stderr='Error from server (NotFound): pods "x" not found', returncode=1)
    with patch("agents.tools.kubectl._run", return_value=unrelated):
        result = kubectl_top_pods(namespace="default")
    assert result["success"] is False
    assert "error_class" not in result
