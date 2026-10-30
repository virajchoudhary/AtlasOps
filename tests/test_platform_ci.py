from __future__ import annotations

import asyncio
import copy
import json
import subprocess
from contextlib import nullcontext
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from scripts import platform_ci


def _is_current_core_path(path: str | Path) -> bool:
    return Path(path).resolve().is_relative_to(platform_ci.REPO_ROOT.resolve())


SOURCE_SHA = "a" * 40
BASE_SHA = "b" * 40
AUDIT_SECRET = "test-placeholder-platform-ci-audit-secret"
API_KEY = "test-placeholder-platform-ci-operator-key"
OPERATOR_ID = "ci-synthetic-operator"
INCIDENT_ID = "inc-platform-ci"
EXECUTION_ID = "c" * 32
HISTORY_ID = "218"


def test_ci_regressions_accept_only_core_from_the_same_checkout() -> None:
    neighbor = platform_ci.REPO_ROOT.parent.parent / "agents" / "coordinator.py"
    assert not _is_current_core_path(neighbor)
    assert _is_current_core_path(platform_ci.REPO_ROOT / "agents" / "coordinator.py")


def test_workflow_installs_and_qualifies_local_setup_helper_dependencies() -> None:
    workflow = yaml.safe_load(
        (platform_ci.REPO_ROOT / ".github/workflows/platform-integration.yml").read_text(
            encoding="utf-8"
        )
    )
    steps = workflow["jobs"]["platform-integration"]["steps"]
    commands = "\n".join(step.get("run", "") for step in steps)

    assert "PyYAML==6.0.3" in commands
    assert "bcrypt==5.0.0" in commands
    assert "from scripts.bcrypt_util import hash_bcrypt, verify_bcrypt" in commands
    assert "assert verify_bcrypt(" in commands


def test_runner_bootstrap_prepares_fixture_credentials_before_docker(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class BootstrapComplete(Exception):
        pass

    actual_run = platform_ci.subprocess.run
    helper_calls = []

    def run_only_secret_helper(command, *args, **kwargs):
        assert command[:2] == [
            platform_ci.sys.executable,
            "scripts/generate_runtime_secrets.py",
        ]
        helper_calls.append(command)
        return actual_run(command, *args, **kwargs)

    monkeypatch.setattr(platform_ci.subprocess, "run", run_only_secret_helper)
    monkeypatch.setattr(platform_ci.tempfile, "tempdir", str(tmp_path))
    runner = platform_ci.Runner(
        _run_identity(),
        tmp_path / "evidence",
        env={"PATH": platform_ci.os.environ.get("PATH", "")},
    )

    def stop_before_docker():
        assert len(helper_calls) == 1
        assert runner.private_dir is not None
        assert runner.secret_dir is not None
        assert not runner.private_dir.is_relative_to(platform_ci.REPO_ROOT)
        key = (runner.private_dir / "fixture-api-key").read_text(encoding="utf-8").strip()
        assert key
        assert runner._fixture_api_key == key
        assert (runner.secret_dir / "llm-api-key.secret").read_text(encoding="utf-8") == key
        assert {path.name for path in runner.secret_dir.glob("*.secret")} == {
            "atlasops-audit-secret.secret",
            "alertmanager-webhook-secret.secret",
            "atlasops-api-key.secret",
            "argocd-user.secret",
            "argocd-pass.secret",
            "grafana-admin-user.secret",
            "grafana-admin-password.secret",
            "llm-api-key.secret",
        }
        assert runner.data["stages"]["runtime_secrets"]["status"] == "completed"
        assert runner.data["fixture"]["model"] == platform_ci.FIXTURE_MODEL
        assert runner.data["fixture"]["external_model_calls"] is False
        assert key not in (runner.evidence_dir / "evidence.json").read_text(encoding="utf-8")
        raise BootstrapComplete

    monkeypatch.setattr(runner, "_prepare_docker_context", stop_before_docker)
    try:
        with pytest.raises(BootstrapComplete):
            runner._execute()
    finally:
        if runner.private_temp is not None:
            runner.private_temp.cleanup()


def _pull_request_event(*, branch: str = "ci/platform-fixture") -> dict[str, object]:
    return {
        "repository": {"full_name": platform_ci.EXPECTED_REPOSITORY},
        "pull_request": {
            "state": "open",
            "head": {
                "sha": SOURCE_SHA,
                "ref": branch,
                "repo": {"full_name": platform_ci.EXPECTED_REPOSITORY},
            },
            "base": {
                "sha": BASE_SHA,
                "ref": "main",
                "repo": {"full_name": platform_ci.EXPECTED_REPOSITORY},
            },
        },
    }


def _identity_env(event_path: Path, *, event_name: str = "pull_request") -> dict[str, str]:
    return {
        "GITHUB_ACTIONS": "true",
        "RUNNER_ENVIRONMENT": "github-hosted",
        "GITHUB_REPOSITORY": platform_ci.EXPECTED_REPOSITORY,
        "GITHUB_RUN_ID": "318",
        "GITHUB_RUN_ATTEMPT": "2",
        "GITHUB_EVENT_NAME": event_name,
        "GITHUB_EVENT_PATH": str(event_path),
        "GITHUB_SHA": SOURCE_SHA,
    }


def _valid_git(args: list[str]) -> str:
    if args[:2] == ["rev-parse", "--verify"] and args[2] == "HEAD":
        return SOURCE_SHA
    if args[:2] == ["remote", "get-url"]:
        return platform_ci.EXPECTED_REPO_URL
    if args[:2] == ["rev-parse", "--verify"]:
        return SOURCE_SHA
    if args[:1] == ["check-ref-format"]:
        return ""
    if args[:1] == ["status"]:
        return ""
    raise AssertionError(f"unexpected Git identity query: {args}")


def _pre_fault_state() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    history = [
        {
            "id": 218,
            "revision": SOURCE_SHA,
            "source": {
                "repoURL": platform_ci.EXPECTED_REPO_URL,
                "path": platform_ci.APP_PATH,
            },
        }
    ]
    application: dict[str, object] = {
        "metadata": {"name": platform_ci.APP_NAME, "namespace": "argocd"},
        "spec": {
            "source": {
                "repoURL": platform_ci.EXPECTED_REPO_URL,
                "path": platform_ci.APP_PATH,
                "targetRevision": SOURCE_SHA,
            }
        },
        "status": {
            "sync": {"status": "Synced", "revision": SOURCE_SHA},
            "history": history,
        },
    }
    deployment: dict[str, object] = {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {
            "name": "frontend",
            "namespace": "atlasops-boutique",
            "uid": "deployment-uid",
            "generation": 7,
            "labels": {"app": "frontend"},
            "annotations": {
                "argocd.argoproj.io/tracking-id": (
                    f"{platform_ci.APP_NAME}:apps/Deployment:"
                    f"{platform_ci.APP_WORKLOAD_NAMESPACE}/{platform_ci.WORKLOAD_NAME}"
                )
            },
        },
        "spec": {
            "replicas": 1,
            "template": {
                "metadata": {"labels": {"app": "frontend"}},
                "spec": {"containers": [{"name": "server", "image": "frontend:source"}]},
            },
        },
    }
    rule: dict[str, object] = {
        "name": platform_ci.ALERT_NAME,
        "query": (
            'max(kube_deployment_status_replicas_available'
            '{namespace="atlasops-boutique", deployment="frontend"}) < 1'
        ),
        "labels": {"atlasops_route": "coordinator"},
    }
    return application, deployment, rule


def _legacy_labeled_pre_fault_state() -> tuple[
    dict[str, object],
    dict[str, object],
    dict[str, object],
]:
    application, deployment, rule = _pre_fault_state()
    metadata = deployment["metadata"]
    assert isinstance(metadata, dict)
    metadata_labels = metadata["labels"]
    assert isinstance(metadata_labels, dict)
    metadata_labels["argocd.argoproj.io/instance"] = platform_ci.APP_NAME
    template = deployment["spec"]["template"]
    assert isinstance(template, dict)
    template_metadata = template["metadata"]
    assert isinstance(template_metadata, dict)
    template_labels = template_metadata["labels"]
    assert isinstance(template_labels, dict)
    template_labels["argocd.argoproj.io/instance"] = platform_ci.APP_NAME
    return application, deployment, rule


def _run_identity() -> platform_ci.RunIdentity:
    return platform_ci.RunIdentity(
        repository=platform_ci.EXPECTED_REPOSITORY,
        source_sha=SOURCE_SHA,
        base_sha=BASE_SHA,
        run_id="318",
        attempt="2",
        event_name="pull_request",
        source_ref="refs/remotes/origin/ci/platform-fixture",
        source_branch="ci/platform-fixture",
    )


def _owner(runner: platform_ci.Runner) -> platform_ci.ClusterOwnership:
    return platform_ci.ClusterOwnership(
        cluster_name=runner.cluster_name,
        context_name=runner.context_name,
        control_plane_id="d" * 64,
        cluster_label=runner.cluster_name,
        role_label="control-plane",
        api_server="https://127.0.0.1:45123",
        api_host_port="45123",
        kube_system_uid="kube-system-uid",
    )


def _owner_docker_double(
    runner: platform_ci.Runner,
    calls: list[tuple[object, ...]],
) -> platform_ci.ClusterOwnership:
    owner = _owner(runner)
    port_bindings = {
        "6443/tcp": [{"HostIp": "127.0.0.1", "HostPort": owner.api_host_port}],
        "30080/tcp": [{"HostIp": "127.0.0.1", "HostPort": "8080"}],
    }
    inspected = {
        "Id": owner.control_plane_id,
        "Name": f"/{runner.cluster_name}-control-plane",
        "State": {"Running": True},
        "Config": {
            "Labels": {
                platform_ci.KIND_CLUSTER_LABEL: runner.cluster_name,
                platform_ci.KIND_NODE_ROLE_LABEL: "control-plane",
            }
        },
        "HostConfig": {"PortBindings": port_bindings},
        "NetworkSettings": {"Ports": port_bindings},
    }

    def docker(*args: object, **_kwargs: object) -> SimpleNamespace:
        calls.append(args)
        if args[0] == "ps":
            container_id = owner.control_plane_id
            if "--no-trunc" not in args:
                container_id = container_id[:12]
            return SimpleNamespace(stdout=f"{container_id}\n", returncode=0)
        if args[0] == "inspect":
            if args[1] != owner.control_plane_id:
                raise platform_ci.PlatformCIError("Docker inspect received a truncated owner ID")
            return SimpleNamespace(stdout=json.dumps([inspected]), returncode=0)
        raise AssertionError(f"unexpected Docker owner command: {args}")

    runner._cluster_list = lambda: {runner.cluster_name}
    runner._docker = docker
    runner._load_kubeconfig_server = lambda: owner.api_server
    runner._kubectl_json = lambda *args, **_kwargs: {
        "kind": "Namespace",
        "metadata": {"name": "kube-system", "uid": owner.kube_system_uid},
    }
    return owner


def test_read_owner_requests_full_untruncated_container_ids(tmp_path: Path) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "read-owner-evidence", env={})
    calls: list[tuple[object, ...]] = []
    expected = _owner_docker_double(runner, calls)

    observed = runner._read_owner()

    assert observed == expected
    ps_calls = [args for args in calls if args[0] == "ps"]
    assert len(ps_calls) == 1
    assert "--no-trunc" in ps_calls[0]
    assert observed.kube_system_uid == "kube-system-uid"


def test_capture_owner_requests_full_untruncated_container_ids(tmp_path: Path) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "capture-owner-evidence", env={})
    calls: list[tuple[object, ...]] = []
    expected = _owner_docker_double(runner, calls)
    runner._record = lambda *_args, **_kwargs: None
    runner._flush = lambda: None

    observed = runner._capture_owner()

    assert observed == expected
    ps_calls = [args for args in calls if args[0] == "ps"]
    assert len(ps_calls) == 2
    assert all("--no-trunc" in args for args in ps_calls)
    assert runner.data["cluster"]["ownership"]["control_plane_id"] == expected.control_plane_id


def test_cluster_cleanup_absence_probe_requests_full_container_ids(tmp_path: Path) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "cleanup-owner-evidence", env={})
    calls: list[tuple[object, ...]] = []
    expected = _owner_docker_double(runner, calls)
    original_docker = runner._docker
    deleted = {"value": False}
    runner._cluster_list = lambda: set() if deleted["value"] else {runner.cluster_name}

    def docker(*args: object, **kwargs: object) -> SimpleNamespace:
        if args[0] == "ps" and deleted["value"]:
            calls.append(args)
            return SimpleNamespace(stdout="", returncode=0)
        return original_docker(*args, **kwargs)

    runner._docker = docker
    runner._kind = lambda *_args, **_kwargs: (
        deleted.update(value=True) or SimpleNamespace(stdout="", returncode=0)
    )
    runner._flush = lambda: None
    runner.owner = expected
    runner.creation_attempted = True
    runner.cluster_created_by_runner = True

    runner._cleanup_cluster()

    ps_calls = [args for args in calls if args[0] == "ps"]
    assert len(ps_calls) == 2
    assert all("--no-trunc" in args for args in ps_calls)
    assert runner.data["cleanup"]["status"] == "verified_deleted"


def _synced_argo_application(started_at: str) -> dict[str, object]:
    return {
        "spec": {"source": {"targetRevision": SOURCE_SHA}},
        "status": {
            "sync": {"status": "Synced", "revision": SOURCE_SHA},
            "operationState": {
                "phase": "Succeeded",
                "startedAt": started_at,
                "operation": {
                    "initiatedBy": {"username": "atlasops"},
                    "sync": {"revision": SOURCE_SHA},
                },
            },
        },
    }


def _receipt_bound_sync() -> tuple[dict[str, object], dict[str, object], dict[str, object]]:
    action = _prepared_action()
    operation = action["patch"][-1]["value"]
    operation["info"] = [{"name": "atlasops-action-id", "value": "d" * 64}]
    digest = platform_ci._prepared_action_digest(action)
    target = action["target"]
    before = _synced_argo_application("1970-01-01T00:01:30Z")
    before["metadata"] = {
        "name": platform_ci.APP_NAME, "namespace": platform_ci.APP_NAMESPACE,
        "uid": target["uid"], "resourceVersion": target["resource_version"],
    }
    before["spec"]["source"] = copy.deepcopy(action["patch"][-2]["value"])
    application = copy.deepcopy(before)
    application["metadata"]["resourceVersion"] = "19"
    application["status"]["operationState"]["startedAt"] = "1970-01-01T00:01:40Z"
    application["status"]["operationState"]["operation"] = copy.deepcopy(operation)
    dispatch = {
        "approval_timestamp": 100.25,
        "mutation_started_timestamp": 100.75,
        "mutation_returned_timestamp": 100.80,
        "action": action,
        "action_digest": digest,
        "action_result": {
            "success": True,
            "atomic_preconditions": True,
            "target": copy.deepcopy(target),
            "action_digest": digest,
            "mutation_receipt": {
                "target": {**target, "resource_version": "18"},
                "source": copy.deepcopy(action["patch"][-2]["value"]),
                "operation": copy.deepcopy(operation),
            },
        },
    }
    return dispatch, before, application


def test_current_same_repository_pr_head_is_accepted(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_pull_request_event()), encoding="utf-8")
    git_calls: list[list[str]] = []

    def git(_root: Path, args: list[str]) -> str:
        git_calls.append(args)
        return _valid_git(args)

    identity = platform_ci.validate_run_identity(
        _identity_env(event_path),
        repo_root=tmp_path,
        git=git,
    )

    assert identity.source_sha == SOURCE_SHA
    assert identity.base_sha == BASE_SHA
    assert identity.source_ref == "refs/remotes/origin/ci/platform-fixture"
    assert ["status", "--porcelain", "--untracked-files=all"] in git_calls


def test_workflow_gates_pr_code_at_job_level_and_keeps_credentials_ephemeral() -> None:
    workflow_path = platform_ci.REPO_ROOT / ".github" / "workflows" / "platform-integration.yml"
    workflow = yaml.safe_load(workflow_path.read_text(encoding="utf-8"))
    job = workflow["jobs"]["platform-integration"]
    condition = job["if"]
    checkout = next(
        step for step in job["steps"]
        if isinstance(step.get("uses"), str) and step["uses"].startswith("actions/checkout@")
    )

    assert workflow["permissions"] == {"contents": "read"}
    assert "github.event.pull_request.head.repo.full_name == github.repository" in condition
    assert "github.event.pull_request.base.ref == 'main'" in condition
    assert "github.event.pull_request.state == 'open'" in condition
    assert checkout["with"]["persist-credentials"] is False


def test_http_json_does_not_forward_operator_key_across_redirect() -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    redirected_headers: list[str | None] = []

    class DestinationHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            redirected_headers.append(self.headers.get("X-AtlasOps-Key"))
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    destination = ThreadingHTTPServer(("127.0.0.1", 0), DestinationHandler)
    destination_thread = Thread(target=destination.serve_forever, daemon=True)
    destination_thread.start()

    requested_paths: list[str] = []
    destination_url = f"http://127.0.0.1:{destination.server_port}/capture"

    class RedirectHandler(BaseHTTPRequestHandler):
        def _redirect(self) -> None:
            requested_paths.append(self.path)
            self.send_response(302)
            self.send_header("Location", destination_url)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"redirect":"blocked"}')

        def do_GET(self) -> None:
            self._redirect()

        def do_POST(self) -> None:
            self._redirect()

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    origin = ThreadingHTTPServer(("127.0.0.1", 0), RedirectHandler)
    origin_thread = Thread(target=origin.serve_forever, daemon=True)
    origin_thread.start()
    try:
        status, payload = platform_ci.http_json(
            f"http://127.0.0.1:{origin.server_port}/operations/decision",
            method="POST",
            headers={"X-AtlasOps-Key": "test-only-operator-key"},
            payload={"decision": "approved"},
            timeout=3,
        )
    finally:
        origin.shutdown()
        destination.shutdown()
        origin.server_close()
        destination.server_close()
        origin_thread.join(timeout=3)
        destination_thread.join(timeout=3)

    assert status == 302
    assert payload == {"redirect": "blocked"}
    assert requested_paths == ["/operations/decision"]
    assert redirected_headers == []


@pytest.mark.parametrize("body", [b"Prometheus Server is Ready.\n", b"OK"])
def test_service_readiness_accepts_plain_text_without_weakening_json_api_reads(
    body: bytes,
    tmp_path: Path,
) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    class ReadyHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), ReadyHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runner = platform_ci.Runner(_run_identity(), tmp_path / "plain-readiness", env={})
    url = f"http://127.0.0.1:{server.server_port}/-/ready"
    try:
        runner._wait_tcp_service("test-service", url, 2)
        with pytest.raises(platform_ci.PlatformCIError, match="UTF-8 JSON"):
            platform_ci.http_json(url)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)

    records = [
        json.loads(line)
        for line in (runner.evidence_dir / "logs" / "http-events.jsonl").read_text().splitlines()
    ]
    assert records[-1]["status"] == 200
    assert records[-1]["path"] == "/-/ready"


def test_history_id_comes_from_correlated_native_tool_messages() -> None:
    messages = [
        {"role": "user", "content": json.dumps({"incident_id": INCIDENT_ID})},
        {
            "role": "assistant",
            "tool_calls": [
                platform_ci._tool_call(
                    "argocd_app_history", {"app": platform_ci.APP_NAME}, "history-native"
                )
            ],
        },
        {
            "role": "tool",
            "tool_call_id": "history-native",
            "content": json.dumps(
                {"success": True, "history": [{"id": 218, "revision": SOURCE_SHA}]}
            ),
        },
    ]
    assert platform_ci.history_id_from_messages(messages, source_sha=SOURCE_SHA) == "218"

    messages[-1]["tool_call_id"] = "uncorrelated"
    with pytest.raises(platform_ci.PlatformCIError, match="one numeric Argo history"):
        platform_ci.history_id_from_messages(messages, source_sha=SOURCE_SHA)


@pytest.mark.parametrize(
    ("service_port", "pod_port", "reported_pod_port"),
    [
        (9099, 7860, 7860),
        (9090, 9090, 9090),
        (9093, 9093, 9093),
        (16686, 16686, 16686),
        pytest.param(9099, 7860, 9099, id="reject-service-port-as-pod-port"),
        pytest.param(9099, 7860, 9999, id="reject-wrong-pod-port"),
    ],
)
def test_port_forward_uses_the_local_port_reported_by_kubectl(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    service_port: int,
    pod_port: int,
    reported_pod_port: int,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "port-forward-evidence", env={})
    runner.kubeconfig = tmp_path / "ci.kubeconfig"
    runner.context_name = "kind-atlasops-ci-fixture"
    runner.child_env = {"PATH": "fixture-path"}
    runner._append_jsonl = lambda *_args, **_kwargs: None
    selected_port = 43127
    commands: list[list[str]] = []
    connections: list[tuple[str, int]] = []

    class PortForwardProcess:
        running = True

        def poll(self) -> int | None:
            return None if self.running else 0

        def terminate(self) -> None:
            self.running = False

        def kill(self) -> None:
            self.running = False

        def wait(self, timeout: float | None = None) -> int:
            self.running = False
            return 0

    process = PortForwardProcess()

    def start_process(command: list[str], **kwargs: object) -> PortForwardProcess:
        commands.append(command)
        stream = kwargs["stdout"]
        stream.write(f"Forwarding from 127.0.0.1:{selected_port} -> {reported_pod_port}\n")
        stream.flush()
        return process

    monkeypatch.setattr(platform_ci.subprocess, "Popen", start_process)
    monkeypatch.setattr(
        platform_ci.socket,
        "create_connection",
        lambda address, timeout: connections.append(address) or nullcontext(),
    )

    try:
        if reported_pod_port != pod_port:
            with pytest.raises(platform_ci.PlatformCIError, match="invalid bound port"):
                runner._start_forward(
                    "coordinator",
                    "default",
                    "atlasops-coordinator-svc",
                    service_port,
                    pod_port=pod_port,
                )
            assert connections == []
            return
        port = runner._start_forward(
            "coordinator",
            "default",
            "atlasops-coordinator-svc",
            service_port,
            pod_port=pod_port,
        )
        assert port == selected_port
        assert commands[0][-1] == f"0:{service_port}"
        assert connections == [("127.0.0.1", selected_port)]
        assert runner.forwards["coordinator"].port == selected_port
    finally:
        runner._stop_forwards()


def test_admin_forwards_bind_the_service_ports_and_validate_the_pod_ports(
    tmp_path: Path,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "admin-forwards", env={})
    calls = []

    def start_forward(name, namespace, service, service_port, *, pod_port):
        calls.append((name, namespace, service, service_port, pod_port))
        return 43127 + len(calls)

    runner._start_forward = start_forward
    runner._start_forwards()

    assert calls == [
        ("coordinator", "default", "atlasops-coordinator-svc", 9099, 7860),
        ("prometheus", "monitoring", "prometheus-kube-prometheus-prometheus", 9090, 9090),
        ("alertmanager", "monitoring", "prometheus-kube-prometheus-alertmanager", 9093, 9093),
        ("jaeger", "jaeger", "jaeger", 16686, 16686),
    ]
    assert runner.data["admin_forwards"] == {
        name: {"bind": "127.0.0.1", "port": 43128 + index}
        for index, (name, *_args) in enumerate(calls)
    }
    assert runner.data["stages"]["admin_forwards"]["status"] == "completed"


def test_live_verifier_receives_only_source_and_owned_cluster_inputs(
    tmp_path: Path,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "live-verifier-evidence", env={})
    runner.kubeconfig = tmp_path / "ci.kubeconfig"
    runner.secret_dir = tmp_path / "secrets"
    commands: list[list[str]] = []

    def run_verifier(command: list[str], **_kwargs: object) -> SimpleNamespace:
        commands.append(command)
        evidence_file = Path(command[command.index("--evidence-file") + 1])
        evidence_file.write_text(
            json.dumps(
                {
                    "classification": platform_ci.FIXTURE_CLASSIFICATION,
                    "source_sha": SOURCE_SHA,
                }
            ),
            encoding="utf-8",
        )
        return SimpleNamespace(stdout="")

    runner._command = run_verifier
    assert runner._verify_live("pre-fault")["source_sha"] == SOURCE_SHA

    command = commands[0]
    assert {
        item for item in command if item.startswith("--")
    } == {
        "--source-sha",
        "--kube-context",
        "--kubeconfig",
        "--operator-key-file",
        "--evidence-file",
    }
    assert str(runner.kubeconfig) in command
    assert not any("http://" in item or "https://" in item for item in command)


def test_forward_cleanup_attempts_every_process_after_kill_wait_timeout(
    tmp_path: Path,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "forward-cleanup", env={})
    attempts: list[tuple[str, str]] = []
    records: list[dict[str, object]] = []

    class ForwardProcess:
        def __init__(self, name: str, *, stuck: bool):
            self.name = name
            self.stuck = stuck
            self.running = True

        def poll(self) -> int | None:
            return None if self.running else 0

        def terminate(self) -> None:
            attempts.append((self.name, "terminate"))

        def kill(self) -> None:
            attempts.append((self.name, "kill"))

        def wait(self, timeout: float) -> int:
            attempts.append((self.name, "wait"))
            if self.stuck:
                raise platform_ci.subprocess.TimeoutExpired(["kubectl", "port-forward"], timeout)
            self.running = False
            return 0

    for name, stuck in (("stuck", True), ("healthy", False)):
        log_path = tmp_path / f"{name}.log"
        stream = log_path.open("w", encoding="utf-8")
        stream.write(f"Forwarding fixture {name}\n")
        stream.flush()
        runner.forwards[name] = platform_ci.Forward(
            name, 43127, ForwardProcess(name, stuck=stuck), log_path, stream
        )
    forwards = list(runner.forwards.values())
    runner._append_jsonl = lambda _path, value: records.append(value)

    with pytest.raises(platform_ci.PlatformCIError, match="port-forward cleanup"):
        runner._stop_forwards()

    assert attempts == [
        ("stuck", "terminate"),
        ("stuck", "wait"),
        ("stuck", "kill"),
        ("stuck", "wait"),
        ("healthy", "terminate"),
        ("healthy", "wait"),
    ]
    assert all(forward.log_stream.closed for forward in forwards)
    assert records[0]["terminated"] is False
    assert records[0]["error_type"] == "TimeoutExpired"
    assert records[1]["terminated"] is True
    assert runner.forwards == {}


def test_argo_timestamp_precision_does_not_replace_operation_identity(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "argo-order-evidence", env={})
    dispatch, before, application = _receipt_bound_sync()
    application["status"]["operationState"]["operation"].pop("info")
    runner._application = lambda: application
    runner._append_jsonl = lambda *_args, **_kwargs: None
    ticks = iter((0.0, 1.0, 301.0))
    monkeypatch.setattr(platform_ci.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(platform_ci.time, "sleep", lambda _seconds: None)

    with pytest.raises(platform_ci.PlatformCIError, match="Argo did not complete"):
        runner._wait_for_application_sync(SOURCE_SHA, dispatch, before=before)

    application["status"]["operationState"]["startedAt"] = "1970-01-01T00:01:40.800Z"
    application["status"]["operationState"]["operation"] = copy.deepcopy(
        dispatch["action_result"]["mutation_receipt"]["operation"]
    )
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(platform_ci.time, "monotonic", lambda: next(ticks))
    assert runner._wait_for_application_sync(SOURCE_SHA, dispatch, before=before) is application


def test_argo_same_second_sync_requires_a_new_operation_on_the_approved_application(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "argo-same-second", env={})
    dispatch, before, application = _receipt_bound_sync()
    runner._application = lambda: application
    ticks = iter((0.0, 1.0))
    monkeypatch.setattr(platform_ci.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(platform_ci.time, "sleep", lambda _seconds: None)

    assert runner._wait_for_application_sync(
        SOURCE_SHA, dispatch, before=before
    ) is application
    assert runner.data["argo_sync_correlation"]["operation_id"] == "d" * 64
    assert runner.data["argo_sync_correlation"]["receipt_resource_version"] == "18"


@pytest.mark.parametrize(
    "invalid_before",
    ["same-operation", "pending-operation", "different-uid", "replaced-later", "replaced-fractional"],
)
def test_argo_same_second_sync_rejects_stale_pending_or_replaced_application(
    invalid_before: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / invalid_before, env={})
    dispatch, before, application = _receipt_bound_sync()
    if invalid_before == "same-operation":
        before["status"]["operationState"]["startedAt"] = (
            application["status"]["operationState"]["startedAt"]
        )
        before["status"]["operationState"]["operation"] = copy.deepcopy(
            application["status"]["operationState"]["operation"]
        )
    elif invalid_before == "pending-operation":
        before["operation"] = {"sync": {"revision": SOURCE_SHA}}
    else:
        before["metadata"]["uid"] = "other-application"
        if invalid_before == "replaced-later":
            application["status"]["operationState"]["startedAt"] = "1970-01-01T00:01:41Z"
        elif invalid_before == "replaced-fractional":
            application["status"]["operationState"]["startedAt"] = "1970-01-01T00:01:40.800Z"
    runner._application = lambda: application
    ticks = iter((0.0, 1.0, 301.0))
    monkeypatch.setattr(platform_ci.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(platform_ci.time, "sleep", lambda _seconds: None)

    with pytest.raises(platform_ci.PlatformCIError):
        runner._wait_for_application_sync(SOURCE_SHA, dispatch, before=before)


def test_main_push_uses_the_exact_event_commit(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(
        json.dumps(
            {
                "repository": {"full_name": platform_ci.EXPECTED_REPOSITORY},
                "ref": "refs/heads/main",
                "after": SOURCE_SHA,
                "before": BASE_SHA,
            }
        ),
        encoding="utf-8",
    )

    identity = platform_ci.validate_run_identity(
        _identity_env(event_path, event_name="push"),
        repo_root=tmp_path,
        git=lambda _root, args: _valid_git(args),
    )

    assert identity.source_sha == SOURCE_SHA
    assert identity.base_sha == BASE_SHA
    assert identity.source_ref == "refs/remotes/origin/main"


def test_foreign_fork_head_is_rejected_before_git_identity_reads(tmp_path: Path) -> None:
    event = _pull_request_event()
    pull_request = event["pull_request"]
    assert isinstance(pull_request, dict)
    head = pull_request["head"]
    assert isinstance(head, dict)
    head["repo"] = {"full_name": "external-contributor/AtlasOps"}
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(event), encoding="utf-8")
    git_calls: list[list[str]] = []

    with pytest.raises(platform_ci.PlatformCIError, match="foreign-fork"):
        platform_ci.validate_run_identity(
            _identity_env(event_path),
            repo_root=tmp_path,
            git=lambda _root, args: git_calls.append(args) or "",
        )

    assert git_calls == []


def test_source_must_remain_the_current_reachable_pr_head(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_pull_request_event()), encoding="utf-8")

    def moved_ref_git(_root: Path, args: list[str]) -> str:
        if args[:2] == ["rev-parse", "--verify"] and args[2].startswith("refs/remotes/"):
            return "c" * 40
        return _valid_git(args)

    with pytest.raises(platform_ci.PlatformCIError, match="current reachable"):
        platform_ci.validate_run_identity(
            _identity_env(event_path),
            repo_root=tmp_path,
            git=moved_ref_git,
        )


def test_source_guard_rejects_a_dirty_checkout(tmp_path: Path) -> None:
    event_path = tmp_path / "event.json"
    event_path.write_text(json.dumps(_pull_request_event()), encoding="utf-8")

    def dirty_git(_root: Path, args: list[str]) -> str:
        if args[:1] == ["status"]:
            return " M app.py"
        return _valid_git(args)

    with pytest.raises(platform_ci.PlatformCIError, match="clean source checkout"):
        platform_ci.validate_run_identity(
            _identity_env(event_path),
            repo_root=tmp_path,
            git=dirty_git,
        )


def test_runner_refuses_local_invocation_before_starting_any_process(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    started: list[object] = []
    monkeypatch.setattr(
        platform_ci.subprocess,
        "run",
        lambda *args, **kwargs: started.append((args, kwargs)),
    )
    evidence_dir = tmp_path / "evidence"

    with pytest.raises(platform_ci.PlatformCIError, match="GitHub-hosted Actions"):
        platform_ci.run_integration(evidence_dir, environ={})

    assert started == []
    evidence = json.loads((evidence_dir / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["status"] == "failed"
    assert evidence["classification"] == "NON_EMPIRICAL_SOFTWARE_FIXTURE"
    assert evidence["certification_status"] == "NOT_CERTIFIED"
    assert evidence["failure"]["stage"] == "identity_guard"


def test_child_runtime_uses_private_kubeconfig_and_drops_ambient_credentials(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    inherited = {
        "PATH": "runner-path",
        "KUBECONFIG": "C:/owner/production.kubeconfig",
        "DOCKER_HOST": "tcp://production-docker.example:2376",
        "AWS_ACCESS_KEY_ID": "owner-aws-key",
        "OPENAI_API_KEY": "owner-model-key",
        "GITHUB_TOKEN": "runner-github-token",
    }
    runner = platform_ci.Runner(_run_identity(), tmp_path / "ambient-evidence", env=inherited)
    original_temporary_directory = platform_ci.tempfile.TemporaryDirectory

    def private_temporary_directory(*args, **kwargs):
        return original_temporary_directory(*args, dir=tmp_path, **kwargs)

    monkeypatch.setattr(
        platform_ci.tempfile,
        "TemporaryDirectory",
        private_temporary_directory,
    )
    runner._ensure_private_area()

    assert runner.child_env["KUBECONFIG"] == str(runner.kubeconfig)
    assert runner.child_env["KUBECONFIG"] != inherited["KUBECONFIG"]
    assert "DOCKER_HOST" not in runner.child_env
    assert "AWS_ACCESS_KEY_ID" not in runner.child_env
    assert "OPENAI_API_KEY" not in runner.child_env
    assert "GITHUB_TOKEN" not in runner.child_env
    assert runner.child_env["ATLASOPS_OPERATOR_ID"] == "ci-synthetic-operator"
    assert runner.child_env["PYTHONDONTWRITEBYTECODE"] == "1"
    assert runner.private_temp is not None
    runner.private_temp.cleanup()


def test_docker_context_rejects_ambient_remote_endpoint_without_contact(tmp_path: Path) -> None:
    runner = platform_ci.Runner(
        _run_identity(),
        tmp_path / "docker-context-evidence",
        env={"ATLASOPS_PLATFORM_CI_DOCKER_SOCKET": "tcp://production-docker.example:2376"},
    )
    commands: list[tuple[object, ...]] = []
    runner._command = lambda *args, **_kwargs: commands.append(args)

    with pytest.raises(platform_ci.PlatformCIError, match="local Docker socket"):
        runner._prepare_docker_context()

    assert commands == []


def test_pre_fault_accepts_default_argocd_tracking_annotation() -> None:
    application, deployment, rule = _pre_fault_state()
    metadata = deployment["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["labels"] == {"app": "frontend"}
    assert "argocd.argoproj.io/instance" not in metadata["labels"]
    template = deployment["spec"]["template"]
    assert isinstance(template, dict)
    template_metadata = template["metadata"]
    assert isinstance(template_metadata, dict)
    assert template_metadata["labels"] == {"app": "frontend"}
    assert "argocd.argoproj.io/instance" not in template_metadata["labels"]

    eligible = platform_ci.validate_pre_fault(
        application,
        deployment,
        rule,
        [],
        source_sha=SOURCE_SHA,
    )

    assert eligible["history_id"] == "218"
    assert eligible["history_revision"] == SOURCE_SHA
    assert eligible["application_revision"] == SOURCE_SHA
    assert eligible["desired_replicas"] == 1


@pytest.mark.parametrize(
    "tracking_id",
    [
        pytest.param(
            "other-app:apps/Deployment:atlasops-boutique/frontend",
            id="wrong-app",
        ),
        pytest.param(
            "atlasops-boutique:extensions/Deployment:atlasops-boutique/frontend",
            id="wrong-api-group",
        ),
        pytest.param(
            "atlasops-boutique:apps/StatefulSet:atlasops-boutique/frontend",
            id="wrong-kind",
        ),
        pytest.param(
            "atlasops-boutique:apps/Deployment:other/frontend",
            id="wrong-namespace",
        ),
        pytest.param(
            "atlasops-boutique:apps/Deployment:atlasops-boutique/frontend-decoy",
            id="wrong-name",
        ),
    ],
)
def test_pre_fault_rejects_nonexact_argocd_tracking_id(tracking_id: str) -> None:
    application, deployment, rule = _pre_fault_state()
    metadata = deployment["metadata"]
    assert isinstance(metadata, dict)
    metadata["labels"] = {"argocd.argoproj.io/instance": platform_ci.APP_NAME}
    annotations = metadata["annotations"]
    assert isinstance(annotations, dict)
    annotations["argocd.argoproj.io/tracking-id"] = tracking_id
    template = deployment["spec"]["template"]
    assert isinstance(template, dict)
    template_metadata = template["metadata"]
    assert isinstance(template_metadata, dict)
    template_metadata["labels"] = {"argocd.argoproj.io/instance": platform_ci.APP_NAME}

    with pytest.raises(platform_ci.PlatformCIError, match="Argo-owned single replica"):
        platform_ci.validate_pre_fault(
            application,
            deployment,
            rule,
            [],
            source_sha=SOURCE_SHA,
        )


def _pending_argo_rollback(selected: dict[str, object]) -> dict[str, object]:
    revision = selected["history_revision"]
    return {
        "token": "apr-test-token",
        "action_digest": "f" * 64,
        "action": {
            "tool": "argocd_rollback",
            "arguments": {"app": platform_ci.APP_NAME, "revision": selected["history_id"]},
            "target": {
                "kind": "Application",
                "namespace": platform_ci.APP_NAMESPACE,
                "name": platform_ci.APP_NAME,
                "uid": "application-uid",
            },
            "verification_target": {
                "kind": "Deployment",
                "namespace": platform_ci.APP_WORKLOAD_NAMESPACE,
                "name": platform_ci.WORKLOAD_NAME,
                "uid": "deployment-uid",
            },
            "patch": [
                {
                    "op": "replace",
                    "path": "/spec/source",
                    "value": {"targetRevision": revision},
                },
                {
                    "op": "add",
                    "path": "/operation",
                    "value": {"sync": {"revision": revision}},
                },
            ],
        },
    }


def test_pending_action_uses_pre_fault_history_revision(tmp_path: Path) -> None:
    application, deployment, rule = _legacy_labeled_pre_fault_state()
    selected = platform_ci.validate_pre_fault(
        application,
        deployment,
        rule,
        [],
        source_sha=SOURCE_SHA,
    )
    runner = platform_ci.Runner(_run_identity(), tmp_path / "pending-action-evidence", env={})
    runner._application = lambda: {"metadata": {"uid": "application-uid"}}
    runner.data["pre_fault_eligibility"] = selected

    action = runner._pending_action(
        _pending_argo_rollback(runner.data["pre_fault_eligibility"]),
        runner.data["pre_fault_eligibility"],
    )

    assert action["arguments"] == {"app": platform_ci.APP_NAME, "revision": "218"}
    assert runner.data["pre_fault_eligibility"]["history_revision"] == SOURCE_SHA
    assert "revision" not in runner.data["pre_fault_eligibility"]


@pytest.mark.parametrize(
    ("patch_index", "revision_path"),
    [
        pytest.param(0, "targetRevision", id="wrong-source-targetRevision"),
        pytest.param(1, "operation-revision", id="wrong-operation-revision"),
    ],
)
def test_pending_action_rejects_each_wrong_patch_revision(
    patch_index: int,
    revision_path: str,
    tmp_path: Path,
) -> None:
    application, deployment, rule = _legacy_labeled_pre_fault_state()
    selected = platform_ci.validate_pre_fault(
        application,
        deployment,
        rule,
        [],
        source_sha=SOURCE_SHA,
    )
    pending = _pending_argo_rollback(selected)
    action = pending["action"]
    assert isinstance(action, dict)
    patch = action["patch"]
    assert isinstance(patch, list)
    value = copy.deepcopy(patch[patch_index]["value"])
    if revision_path == "targetRevision":
        value["targetRevision"] = BASE_SHA
    else:
        value["sync"]["revision"] = BASE_SHA
    patch[patch_index]["value"] = value
    runner = platform_ci.Runner(_run_identity(), tmp_path / "pending-action-wrong-evidence", env={})
    runner._application = lambda: {"metadata": {"uid": "application-uid"}}

    with pytest.raises(platform_ci.PlatformCIError, match="selected committed revision"):
        runner._pending_action(pending, selected)


def test_fault_is_blocked_without_rule_with_duplicate_history_or_existing_alert() -> None:
    application, deployment, _rule = _pre_fault_state()
    status = application["status"]
    assert isinstance(status, dict)
    history = status["history"]
    assert isinstance(history, list)
    history.append(dict(history[0]))
    _application, _deployment, rule = _pre_fault_state()

    with pytest.raises(platform_ci.PlatformCIError, match="exactly one eligible"):
        platform_ci.validate_pre_fault(
            application,
            deployment,
            rule,
            [],
            source_sha=SOURCE_SHA,
        )

    application, deployment, rule = _pre_fault_state()
    with pytest.raises(platform_ci.PlatformCIError, match="availability rule is not loaded"):
        platform_ci.validate_pre_fault(
            application,
            deployment,
            None,
            [],
            source_sha=SOURCE_SHA,
        )

    application, deployment, rule = _pre_fault_state()
    active_alert = {
        "labels": {
            "alertname": platform_ci.ALERT_NAME,
            "namespace": "atlasops-boutique",
            "deployment": "frontend",
        }
    }
    with pytest.raises(platform_ci.PlatformCIError, match="already active"):
        platform_ci.validate_pre_fault(
            application,
            deployment,
            rule,
            [active_alert],
            source_sha=SOURCE_SHA,
        )


def test_failed_run_never_deletes_a_cluster_after_owner_identity_changes(tmp_path: Path) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "evidence", env={})
    expected = _owner(runner)
    observed = platform_ci.ClusterOwnership(
        **{**expected.__dict__, "kube_system_uid": "replacement-cluster"}
    )
    deletes: list[tuple[object, ...]] = []

    def fail_after_cluster_creation() -> None:
        runner.creation_attempted = True
        runner.cluster_created_by_runner = True
        runner.owner = expected
        runner._read_owner = lambda: observed
        runner._kind = lambda *args, **_kwargs: deletes.append(args)
        raise platform_ci.PlatformCIError("fixture setup failed")

    runner._execute = fail_after_cluster_creation
    with pytest.raises(platform_ci.PlatformCIError, match="fixture setup failed"):
        runner.run()

    evidence = json.loads((tmp_path / "evidence" / "evidence.json").read_text(encoding="utf-8"))
    assert evidence["cleanup"]["status"] == "ownership_mismatch_no_delete"
    assert evidence["cleanup"]["deleted"] is False
    assert deletes == []


def test_failed_partial_creation_without_owner_proof_is_never_deleted(tmp_path: Path) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "partial-evidence", env={})
    deletes: list[tuple[object, ...]] = []

    def fail_during_owner_capture() -> None:
        runner.creation_attempted = True
        runner.cluster_created_by_runner = True
        runner.owner = None
        runner._kind = lambda *args, **_kwargs: deletes.append(args)
        raise platform_ci.PlatformCIError("owner receipt was incomplete")

    runner._execute = fail_during_owner_capture
    with pytest.raises(platform_ci.PlatformCIError, match="owner receipt"):
        runner.run()

    evidence = json.loads(
        (tmp_path / "partial-evidence" / "evidence.json").read_text(encoding="utf-8")
    )
    assert evidence["cleanup"]["status"] == "ownership_unverified_no_delete"
    assert evidence["cleanup"]["deleted"] is False
    assert deletes == []


def test_owner_checked_cluster_cleanup_runs_when_forward_shutdown_times_out(
    tmp_path: Path,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "forward-timeout-evidence", env={})
    expected = _owner(runner)
    deletes: list[tuple[object, ...]] = []

    def fail_during_forward_readiness() -> None:
        runner.creation_attempted = True
        runner.cluster_created_by_runner = True
        runner.owner = expected
        raise platform_ci.PlatformCIError("coordinator did not become ready")

    def fail_stopping_forwards() -> None:
        raise platform_ci.subprocess.TimeoutExpired(["kubectl", "port-forward"], 5)

    runner._execute = fail_during_forward_readiness
    runner._stop_forwards = fail_stopping_forwards
    runner._read_owner = lambda: expected
    runner._kind = lambda *args, **_kwargs: deletes.append(args) or SimpleNamespace(stdout="")
    runner._cluster_list = lambda: set()
    runner._docker = lambda *_args, **_kwargs: SimpleNamespace(stdout="", returncode=0)

    with pytest.raises(platform_ci.PlatformCIError, match="coordinator did not become ready"):
        runner.run()

    evidence = json.loads(
        (tmp_path / "forward-timeout-evidence" / "evidence.json").read_text(encoding="utf-8")
    )
    assert evidence["cleanup"]["ownership_rechecked"] is True
    assert evidence["cleanup"]["status"] == "verified_deleted"
    assert evidence["cleanup"]["verified_absent"] is True
    assert evidence["cleanup_errors"][0]["stage"] == "port_forward_cleanup"
    assert deletes == [("delete", "cluster", "--name", runner.cluster_name)]


@pytest.fixture
def completed_runner(tmp_path: Path, monkeypatch):
    def forbidden_process(*_args, **_kwargs):
        raise AssertionError("Cleanup regressions must not start a subprocess")

    monkeypatch.setattr(platform_ci.subprocess, "run", forbidden_process)
    monkeypatch.setattr(platform_ci.subprocess, "Popen", forbidden_process)
    runner = platform_ci.Runner(_run_identity(), tmp_path / "evidence", env={})
    owner = _owner(runner)
    deletes = []

    def complete_workflow():
        runner.creation_attempted = True
        runner.cluster_created_by_runner = True
        runner.owner = owner
        runner.data["status"] = "passed"

    runner._execute = complete_workflow
    runner._kind = lambda *args, **_kwargs: (
        deletes.append(args) or SimpleNamespace(stdout="", returncode=0)
    )
    runner._cluster_list = lambda: set()
    runner._docker = lambda *_args, **_kwargs: SimpleNamespace(stdout="", returncode=0)
    runner._read_owner = lambda: owner
    return runner, owner, deletes


@pytest.mark.parametrize(
    ("variant", "cleanup_status"),
    [
        ("changed_owner", "ownership_mismatch_no_delete"),
        ("missing_owner", "ownership_unverified_no_delete"),
        ("unverified_creation", "ownership_unverified_no_delete"),
    ],
)
def test_completed_workflow_cannot_pass_without_verified_cleanup(
    completed_runner, variant: str, cleanup_status: str
):
    runner, owner, deletes = completed_runner
    complete_workflow = runner._execute

    def complete_without_owned_cleanup():
        complete_workflow()
        if variant == "missing_owner":
            runner.owner = None
        elif variant == "unverified_creation":
            runner.cluster_created_by_runner = False
        else:
            runner._read_owner = lambda: platform_ci.ClusterOwnership(
                **{**owner.__dict__, "kube_system_uid": "replacement-system"}
            )

    runner._execute = complete_without_owned_cleanup
    with pytest.raises(platform_ci.PlatformCIError, match="cleanup"):
        runner.run()

    evidence = json.loads((runner.evidence_dir / "evidence.json").read_text())
    assert evidence["status"] == "failed"
    assert evidence["cleanup"]["status"] == cleanup_status
    assert evidence["cleanup"]["deleted"] is False
    assert evidence["cleanup"]["verified_absent"] is False
    assert deletes == []


def test_failed_absence_probe_cannot_establish_cleanup_success(completed_runner):
    runner, _owner_receipt, deletes = completed_runner
    runner._docker = lambda *_args, **_kwargs: SimpleNamespace(
        stdout="", stderr="fixture Docker inspection failed", returncode=1
    )

    with pytest.raises(platform_ci.PlatformCIError, match="cleanup"):
        runner.run()

    evidence = json.loads((runner.evidence_dir / "evidence.json").read_text())
    assert evidence["status"] == "failed"
    assert evidence["cleanup"]["status"] == "cleanup_unverified"
    assert evidence["cleanup"]["verified_absent"] is False
    assert len(deletes) == 1


def test_completed_workflow_with_verified_cleanup_still_succeeds(completed_runner):
    runner, _owner_receipt, deletes = completed_runner

    result = runner.run()

    evidence = json.loads(result.read_text())
    assert evidence["status"] == "passed"
    assert evidence["cleanup"]["status"] == "verified_deleted"
    assert evidence["cleanup"]["verified_absent"] is True
    assert deletes == [("delete", "cluster", "--name", runner.cluster_name)]


@pytest.mark.parametrize("diagnostic_timeout", [False, True])
def test_setup_failure_retains_sanitized_resource_status_before_cleanup(
    completed_runner, monkeypatch, diagnostic_timeout: bool
) -> None:
    runner, owner_receipt, deletes = completed_runner
    runner._execute = runner._setup_platform
    runner.owner = owner_receipt
    runner.creation_attempted = True
    runner.cluster_created_by_runner = True
    runner.secret_values.append("fixture-private-credential")
    commands = []

    def run(command, **_kwargs):
        commands.append(command)
        if command[0] == "bash":
            return subprocess.CompletedProcess(command, 1, "", "context deadline exceeded")
        assert command[0] == "kubectl"
        assert not deletes
        if diagnostic_timeout and "pods" in command:
            raise subprocess.TimeoutExpired(command, 20)
        return subprocess.CompletedProcess(
            command, 0, "Pending fixture-private-credential\n", ""
        )

    monkeypatch.setattr(subprocess, "run", run)

    with pytest.raises(platform_ci.PlatformCIError, match="local platform setup failed"):
        runner.run()

    diagnostics = [command for command in commands if command[0] == "kubectl"]
    assert len(diagnostics) == 3
    assert all("get" in command for command in diagnostics)
    assert all("--all-namespaces" in command for command in diagnostics)
    assert all(any(arg.startswith("custom-columns=") for arg in command) for command in diagnostics)
    assert all("secrets" not in command and "logs" not in command for command in diagnostics)
    assert {command[command.index("get") + 1] for command in diagnostics} == {
        "pods", "deployments,statefulsets,jobs", "events"
    }
    evidence = json.loads((runner.evidence_dir / "evidence.json").read_text())
    assert evidence["status"] == "failed"
    assert evidence["failure"]["stage"] == "platform_setup"
    assert evidence["cleanup"]["status"] == "verified_deleted"
    log = (runner.evidence_dir / "logs" / "commands.jsonl").read_text()
    assert "fixture-private-credential" not in log
    assert "Pending" in log
    assert deletes == [("delete", "cluster", "--name", runner.cluster_name)]


def test_private_temp_cleanup_runs_when_cluster_cleanup_raises(tmp_path: Path) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "cleanup-error-evidence", env={})
    cleanup_calls: list[str] = []
    runner.private_temp = SimpleNamespace(cleanup=lambda: cleanup_calls.append("private-temp"))
    runner._execute = lambda: None
    runner._stop_forwards = lambda: None

    def fail_cluster_cleanup() -> None:
        raise RuntimeError("cluster cleanup probe failed")

    runner._cleanup_cluster = fail_cluster_cleanup
    runner._flush = lambda: None

    with pytest.raises(platform_ci.PlatformCIError, match="cluster cleanup probe failed"):
        runner.run()

    assert cleanup_calls == ["private-temp"]
    assert runner.data["cleanup"]["status"] == "cleanup_unverified"
    assert runner.data["cleanup"]["deleted"] is False


def test_failed_runner_evidence_redacts_generated_and_pending_credentials(tmp_path: Path) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "failed-evidence", env={})
    fixture_key = "fixture-key-never-store-this"
    approval_token = "apr-NeverArchiveThisToken"

    def fail_with_secrets() -> None:
        runner.secret_values.extend([fixture_key, approval_token])
        raise RuntimeError(f"failed with {fixture_key} and Bearer {approval_token}")

    runner._execute = fail_with_secrets
    with pytest.raises(platform_ci.PlatformCIError):
        runner.run()

    evidence_text = (tmp_path / "failed-evidence" / "evidence.json").read_text(encoding="utf-8")
    assert fixture_key not in evidence_text
    assert approval_token not in evidence_text
    assert "NON_EMPIRICAL_SOFTWARE_FIXTURE" in evidence_text
    assert "NOT_CERTIFIED" in evidence_text
    assert '"status": "failed"' in evidence_text


def test_pending_incident_timeout_reports_redacted_last_observation_error(
    tmp_path: Path,
    monkeypatch,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "timeout-evidence", env={})
    runner.secret_values.append("ci-private-value")

    def unavailable() -> None:
        raise platform_ci.PlatformCIError("Bearer ci-private-value")

    ticks = iter((0.0, 1.0, 421.0))
    monkeypatch.setattr(platform_ci.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(platform_ci.time, "sleep", lambda _seconds: None)
    runner._deployment = unavailable

    with pytest.raises(platform_ci.PlatformCIError, match="last observation error") as failure:
        runner._wait_for_pending_incident("2026-10-29T00:00:00+00:00")

    assert "Bearer [REDACTED]" in str(failure.value)
    assert "ci-private-value" not in str(failure.value)


def _image_identity_doubles(
    runner: platform_ci.Runner,
    *,
    coordinator: dict[str, object] | None = None,
    pod_image_id: str | None = None,
) -> tuple[list[tuple[str, ...]], str, str]:
    image = f"atlasops:{SOURCE_SHA}"
    docker_config_id = "sha256:" + "a" * 64
    runtime_manifest_digest = "sha256:" + "b" * 64
    rootfs_layer = "sha256:" + "c" * 64
    config = {
        "User": "10001:10001",
        "WorkingDir": "/app",
        "Entrypoint": ["python"],
        "Cmd": ["-m", "app"],
        "Env": ["PATH=/usr/local/bin"],
        "Labels": {"org.opencontainers.image.revision": SOURCE_SHA},
    }
    coordinator = coordinator or {
        "metadata": {
            "name": "atlasops-coordinator",
            "namespace": "default",
            "labels": {"app.kubernetes.io/version": SOURCE_SHA},
        },
        "spec": {
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/version": SOURCE_SHA}},
                "spec": {"containers": [{"name": "coordinator", "image": image}]},
            }
        },
    }
    frontend = _pre_fault_state()[1]
    pod = {
        "metadata": {
            "namespace": "default",
            "labels": {
                "app.kubernetes.io/name": "atlasops-coordinator",
                "app.kubernetes.io/version": SOURCE_SHA,
            },
        },
        "spec": {"containers": [{"name": "coordinator", "image": image}]},
        "status": {
            "containerStatuses": [
                {
                    "name": "coordinator",
                    "ready": True,
                    "image": image,
                    "imageID": pod_image_id or f"containerd://{runtime_manifest_digest}",
                }
            ]
        },
    }
    docker_inspect = [
        {"Id": docker_config_id, "Config": config, "RootFS": {"Layers": [rootfs_layer]}}
    ]
    cri_inspect = {
        "status": {"id": runtime_manifest_digest, "repoDigests": []},
        "info": {
            "imageSpec": {
                "config": config,
                "rootfs": {"diff_ids": [rootfs_layer]},
            }
        },
    }
    kubectl_calls: list[tuple[str, ...]] = []

    def kubectl(*args: str, **_kwargs: object) -> SimpleNamespace:
        kubectl_calls.append(args)
        if args[:3] == ("get", "deployment", "atlasops-coordinator"):
            response: dict[str, object] = coordinator
        elif args[:3] == ("get", "deployment", "frontend"):
            response = frontend
        elif args[:2] == ("get", "pods"):
            response = {"items": [pod]}
        else:
            raise AssertionError(f"unexpected Kubernetes call in image identity test: {args}")
        return SimpleNamespace(stdout=json.dumps(response))

    def docker(*args: str, **_kwargs: object) -> SimpleNamespace:
        if args[:2] == ("image", "inspect"):
            return SimpleNamespace(stdout=json.dumps(docker_inspect))
        if args[:1] == ("exec",):
            return SimpleNamespace(stdout=json.dumps(cri_inspect))
        raise AssertionError(f"unexpected Docker call in image identity test: {args}")

    runner.owner = _owner(runner)
    runner._kubectl = kubectl
    runner._docker = docker
    return kubectl_calls, docker_config_id, runtime_manifest_digest


def test_image_identity_binds_rootfs_and_runtime_digest_without_equating_config_id(
    tmp_path: Path,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "image-evidence", env={})
    kubectl_calls, docker_config_id, runtime_manifest_digest = _image_identity_doubles(runner)

    proof = runner._verify_image_identity()

    assert kubectl_calls == [
        ("get", "deployment", "atlasops-coordinator", "-n", "default", "-o", "json"),
        (
            "get",
            "pods",
            "-n",
            "default",
            "-l",
            "app.kubernetes.io/name=atlasops-coordinator",
            "-o",
            "json",
        ),
    ]
    assert proof["docker_config_id"] == docker_config_id
    assert proof["cri_runtime_id"] == runtime_manifest_digest
    assert proof["pod_image_digest_matches_cri_reference"] is True
    assert proof["cri_rootfs_matches_docker"] is True


def test_image_identity_rejects_a_pod_digest_not_in_the_loaded_cri_image(
    tmp_path: Path,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "image-mismatch-evidence", env={})
    kubectl_calls, _, _ = _image_identity_doubles(
        runner,
        pod_image_id=f"containerd://sha256:{'d' * 64}",
    )

    with pytest.raises(platform_ci.PlatformCIError, match="not represented"):
        runner._verify_image_identity()

    assert kubectl_calls[0][:3] == ("get", "deployment", "atlasops-coordinator")


def test_image_identity_refuses_a_coordinator_deployment_with_wrong_source_image(
    tmp_path: Path,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "wrong-image-evidence", env={})
    wrong_deployment = {
        "metadata": {
            "name": "atlasops-coordinator",
            "namespace": "default",
            "labels": {"app.kubernetes.io/version": BASE_SHA},
        },
        "spec": {
            "template": {
                "metadata": {"labels": {"app.kubernetes.io/version": BASE_SHA}},
                "spec": {"containers": [{"name": "coordinator", "image": f"atlasops:{BASE_SHA}"}]},
            }
        },
    }
    kubectl_calls, _, _ = _image_identity_doubles(
        runner,
        coordinator=wrong_deployment,
    )

    with pytest.raises(platform_ci.PlatformCIError, match="coordinator Deployment"):
        runner._verify_image_identity()

    assert kubectl_calls == [
        ("get", "deployment", "atlasops-coordinator", "-n", "default", "-o", "json")
    ]


def test_fault_patch_atomically_uses_recreate_and_preserves_source_fields(
    tmp_path: Path,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "fault-evidence", env={})
    before = _pre_fault_state()[1]
    metadata = before["metadata"]
    metadata.update(uid="frontend-ci-uid", resourceVersion="41")
    source_strategy = {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
    }
    before["spec"]["selector"] = {"matchLabels": {"app": "frontend"}}
    before["spec"]["strategy"] = copy.deepcopy(source_strategy)
    before["spec"]["template"]["spec"]["serviceAccountName"] = "frontend-account"
    before["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] = "IfNotPresent"
    before["spec"]["template"]["spec"]["containers"].append(
        {"name": "metrics", "image": "metrics:source"}
    )
    deployment_reads = [before]
    deployment_observations: list[dict[str, object]] = []
    patches: list[list[dict[str, object]]] = []

    def get_deployment() -> dict[str, object]:
        assert deployment_reads
        deployment = deployment_reads.pop(0)
        deployment_observations.append(deployment)
        return deployment

    def kubectl(*args: str, **_kwargs: object) -> SimpleNamespace:
        patch = json.loads(args[args.index("-p") + 1])
        assert isinstance(patch, list)
        patches.append(patch)
        after = copy.deepcopy(before)
        server = next(
            item
            for item in after["spec"]["template"]["spec"]["containers"]
            if item["name"] == "server"
        )
        for operation in patch:
            if operation["op"] not in {"add", "replace"}:
                continue
            if operation["path"] == "/spec/strategy":
                after["spec"]["strategy"] = operation["value"]
            elif operation["path"].endswith("/imagePullPolicy"):
                server["imagePullPolicy"] = operation["value"]
            elif operation["path"].endswith("/image"):
                server["image"] = operation["value"]
            else:
                raise AssertionError(f"unexpected mutating JSON Patch path: {operation['path']}")
        deployment_reads.append(after)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    runner._verify_owner_unchanged = lambda: None
    runner._deployment = get_deployment
    runner._docker = lambda *_args, **_kwargs: SimpleNamespace(returncode=1, stdout="", stderr="")
    runner._kubectl = kubectl

    original, invalid_image, _started = runner._fault_deployment()

    assert original is before
    assert len(patches) == 1
    patch = patches[0]
    assert {"op": "test", "path": "/metadata/uid", "value": "frontend-ci-uid"} in patch
    assert {"op": "test", "path": "/metadata/resourceVersion", "value": "41"} in patch
    assert {"op": "test", "path": "/spec/strategy", "value": source_strategy} in patch
    assert {
        "op": "replace",
        "path": "/spec/strategy",
        "value": {"type": "Recreate"},
    } in patch
    assert not any(
        operation["op"] in {"add", "replace"} and operation["path"] == "/spec"
        for operation in patch
    )
    assert runner.data["fault"]["changed_fields"] == [
        "/spec/template/spec/containers/server/image",
        "/spec/template/spec/containers/server/imagePullPolicy",
        "/spec/strategy",
    ]
    assert runner.data["fault"]["original_strategy"] == source_strategy
    assert runner.data["fault"]["fault_image"] == invalid_image
    after = deployment_observations[-1]
    after_server = next(
        item
        for item in after["spec"]["template"]["spec"]["containers"]
        if item["name"] == "server"
    )
    assert after_server["image"] == invalid_image
    assert after_server["imagePullPolicy"] == "Never"
    assert after["spec"]["selector"] == before["spec"]["selector"]
    assert after["spec"]["template"]["spec"]["serviceAccountName"] == "frontend-account"
    assert after["spec"]["template"]["spec"]["containers"][1] == {
        "name": "metrics",
        "image": "metrics:source",
    }
    assert after["metadata"]["labels"] == before["metadata"]["labels"]
    assert after["spec"]["template"]["metadata"]["labels"] == before["spec"]["template"]["metadata"]["labels"]


@pytest.mark.parametrize(
    ("field", "message"),
    [
        ("strategy", "strategy"),
        ("image", "image"),
        ("imagePullPolicy", "imagePullPolicy"),
    ],
)
def test_argo_reconciliation_requires_the_original_frontend_spec_fields(
    tmp_path: Path,
    field: str,
    message: str,
) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "recovery-evidence", env={})
    before = _pre_fault_state()[1]
    before["metadata"]["uid"] = "frontend-ci-uid"
    before["spec"]["strategy"] = {
        "type": "RollingUpdate",
        "rollingUpdate": {"maxUnavailable": 0, "maxSurge": 1},
    }
    before["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] = "IfNotPresent"
    recovered = copy.deepcopy(before)

    runner._deployment = lambda: recovered
    runner._verify_frontend_source_restored(before)

    if field == "strategy":
        recovered["spec"]["strategy"] = {"type": "Recreate"}
    elif field == "image":
        recovered["spec"]["template"]["spec"]["containers"][0]["image"] = "fault:image"
    else:
        recovered["spec"]["template"]["spec"]["containers"][0]["imagePullPolicy"] = "Never"

    with pytest.raises(platform_ci.PlatformCIError, match=message):
        runner._verify_frontend_source_restored(before)


def _healthy_verification() -> dict[str, object]:
    target = "atlasops-boutique/frontend"
    checks = [
        {
            "name": "operational_deployment_current",
            "target": target,
            "passed": True,
            "observed": {
                "uid": "frontend-deployment-uid",
                "generation": 9,
                "observed_generation": 9,
                "desired_replicas": 1,
                "available_replicas": 1,
            },
        }
    ]
    for metric_name in (
        "kube_deployment_spec_replicas",
        "kube_deployment_status_replicas_available",
    ):
        checks.append(
            {
                "name": f"operational_metric_{metric_name}_fresh",
                "target": target,
                "passed": True,
                "observed": {
                    "value": 1,
                    "source_sample_age_at_observation_seconds": 4,
                    "max_sample_age_seconds": 120,
                    "query": (
                        f'{metric_name}{{namespace="atlasops-boutique",deployment="frontend"}}'
                    ),
                },
            }
        )
    checks.extend(
        [
            {
                "name": "operational_kube_state_metrics_recovered",
                "passed": True,
                "observed": {
                    "deployment_spec_replicas": 1,
                    "deployment_status_replicas_available": 1,
                },
            },
            {"name": "operational_alert_cleared", "target": target, "passed": True},
        ]
    )
    return {
        "env_resolved": True,
        "verification_status": "passed",
        "verification_timestamp": "2026-10-29T10:00:00+00:00",
        "checks": checks,
    }


def test_authoritative_verifier_requires_current_uid_replicas_fresh_samples_and_clear_alert() -> None:
    result = platform_ci.validate_verification(
        _healthy_verification(),
        expected_uid="frontend-deployment-uid",
    )

    assert result["deployment"]["observed_generation"] == 9
    assert len(result["fresh_metric_samples"]) == 2
    assert result["alert_clearance"]["passed"] is True

    stale = _healthy_verification()
    checks = stale["checks"]
    assert isinstance(checks, list)
    checks[1]["observed"]["source_sample_age_at_observation_seconds"] = 121
    with pytest.raises(platform_ci.PlatformCIError, match="fresh scoped"):
        platform_ci.validate_verification(
            stale,
            expected_uid="frontend-deployment-uid",
        )


def _prepared_action() -> dict[str, object]:
    from agents.action_execution import PreparedAction, ResourceIdentity

    source = {
        "repoURL": platform_ci.EXPECTED_REPO_URL,
        "path": platform_ci.APP_PATH,
        "targetRevision": SOURCE_SHA,
    }
    patch = [
        {"op": "test", "path": "/spec/source", "value": source},
        {"op": "replace", "path": "/spec/source", "value": source},
        {
            "op": "add",
            "path": "/operation",
            "value": {
                "initiatedBy": {"username": "atlasops"},
                "sync": {"revision": SOURCE_SHA, "prune": False},
            },
        },
    ]
    return PreparedAction(
        "argocd_rollback",
        json.dumps({"app": platform_ci.APP_NAME, "revision": HISTORY_ID}, separators=(",", ":")),
        ResourceIdentity(
            "https://127.0.0.1:45123",
            "kind-atlasops-ci-318-2",
            "argoproj.io/v1alpha1",
            "Application",
            "argocd",
            platform_ci.APP_NAME,
            "application-uid",
            "17",
        ),
        json.dumps(patch, sort_keys=True, separators=(",", ":")),
        ResourceIdentity(
            "https://127.0.0.1:45123",
            "kind-atlasops-ci-318-2",
            "apps/v1",
            "Deployment",
            platform_ci.APP_WORKLOAD_NAMESPACE,
            platform_ci.WORKLOAD_NAME,
            "frontend-deployment-uid",
            "42",
        ),
    ).approval_action


def _actual_operations_export(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    include_mutation_pair: bool = True,
    mutation_started_count: int = 1,
    mutation_returned_count: int = 1,
    mutation_return_success: bool = True,
    mutation_return_action_matches: bool = True,
    mutation_return_approval_matches: bool = True,
    initial_alert: dict[str, object] | None = None,
) -> dict[str, object]:
    import importlib

    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    monkeypatch.setenv("ATLASOPS_OPERATOR_ID", OPERATOR_ID)
    monkeypatch.setenv("ATLASOPS_SOURCE_SHA", SOURCE_SHA)
    monkeypatch.setenv("ATLASOPS_RUNTIME_DATA_DIR", str(tmp_path / "runtime"))
    monkeypatch.setenv("ATLASOPS_AUDIT_SECRET", AUDIT_SECRET)
    monkeypatch.setenv("ATLASOPS_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("POSTMORTEM_DIR", str(tmp_path / "postmortems"))
    monkeypatch.delenv("SLACK_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("DISCORD_WEBHOOK_URL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_ENDPOINT", raising=False)

    import agents.coordinator as coordinator
    from agents.approval import ApprovalGate
    from agents.audit import require_audit_log
    from agents.circuit_breaker import CircuitBreaker
    from agents.execution_journal import ExecutionProgress, current_execution, get_execution_journal
    from agents.operations_api import operations_lifespan, operations_router

    assert _is_current_core_path(coordinator.__file__)
    monkeypatch.setattr(coordinator, "_RUNTIME_API_KEY", API_KEY)

    gate = ApprovalGate(timeout_seconds=60)
    breaker = CircuitBreaker()
    app = FastAPI(lifespan=operations_lifespan)
    app.include_router(
        operations_router(
            gate=lambda: gate,
            breaker=lambda: breaker,
            require_key=coordinator._require_runtime_api_key,
        )
    )

    with TestClient(app) as client:
        journal = get_execution_journal()
        journal.begin(INCIDENT_ID, EXECUTION_ID, alert=initial_alert or {}, anchors=None)
        audit = require_audit_log()
        prepared = _prepared_action()
        for role, tools in {
            "triage": ("alertmanager_list_alerts",),
            "diagnosis": ("argocd_app_history", "kubectl_describe"),
            "remediation": ("argocd_rollback",),
        }.items():
            for tool in tools:
                audit.record(
                    INCIDENT_ID,
                    role,
                    "tool_result",
                    tool_name=tool,
                    tool_args=prepared["arguments"] if role == "remediation" else {"fixture": True},
                    approved_by=OPERATOR_ID if role == "remediation" else "",
                    policy_check="approved_exact_action" if role == "remediation" else "allowed",
                )
            audit.record(INCIDENT_ID, role, "role_completed", result_summary="fixture role result")

        request = gate.request_action(
            incident_id=INCIDENT_ID,
            severity="P1",
            action=prepared,
        )
        approval = {
            "mode": "approve",
            "severity": "P1",
            "triage_severity": "P1",
            "action_digest": request.action_digest,
            "action_summary": request.action_summary,
            "action": request.to_dict()["action"],
            "expires_at": request.to_dict()["expires_at"],
        }
        journal.note(
            EXECUTION_ID,
            phase="approval",
            event="action_prepared",
            patch={"prepared_action": prepared},
        )
        journal.note(
            EXECUTION_ID,
            phase="approval",
            event="approval_requested",
            patch={"approval": approval},
        )
        audit.record(
            INCIDENT_ID,
            "remediation",
            "approval_requested",
            tool_name="argocd_rollback",
            tool_args=prepared["arguments"],
            result_summary=str(request.action_summary),
            policy_check="requires_approval",
        )
        decision = client.post(
            "/operations/decision",
            headers={"X-AtlasOps-Key": API_KEY},
            json={
                "token": request.token,
                "action_digest": request.action_digest,
                "decision": "approved",
            },
        )
        assert decision.status_code == 200
        assert decision.json()["ok"] is True

        approval.update(decision="approved", approved_by=OPERATOR_ID)
        journal.note(
            EXECUTION_ID,
            phase="approval",
            event="approval_decided",
            patch={"approval": approval},
        )
        action_approval = {**approval, "status": "approved"}
        action_output = {
            "success": True,
            "action_digest": request.action_digest,
            "target": prepared["target"],
            "atomic_preconditions": True,
        }
        if include_mutation_pair:
            for _ in range(mutation_started_count):
                journal.note(
                    EXECUTION_ID,
                    phase="applying",
                    event="mutation_started",
                    patch={"prepared_action": prepared, "approval": action_approval},
                )
            returned_action = (
                prepared
                if mutation_return_action_matches
                else {
                    **prepared,
                    "arguments": {"app": platform_ci.APP_NAME, "revision": "999"},
                }
            )
            returned_approval = (
                action_approval
                if mutation_return_approval_matches
                else {**action_approval, "action_digest": "f" * 64}
            )
            returned_output = {**action_output, "success": mutation_return_success}
            for _ in range(mutation_returned_count):
                journal.note(
                    EXECUTION_ID,
                    phase="verification",
                    event="mutation_returned",
                    patch={
                        "prepared_action": returned_action,
                        "approval": returned_approval,
                        "action_result": returned_output,
                    },
                )

        action_record = {
            "step": 1,
            "tool": "argocd_rollback",
            "args": prepared["arguments"],
            "output": action_output,
            "success": True,
            "prepared_action": prepared,
            "approval": action_approval,
        }
        import agents.tools as tool_package

        assert _is_current_core_path(tool_package.__file__)
        comms_module = importlib.import_module("agents.tools.comms")
        assert _is_current_core_path(comms_module.__file__)
        import agents.telemetry as telemetry

        monkeypatch.setattr(comms_module, "SLACK_WEBHOOK", "")
        monkeypatch.setattr(comms_module, "DISCORD_WEBHOOK", "")
        monkeypatch.setattr(
            telemetry,
            "trace_span",
            lambda *_args, **_kwargs: nullcontext(),
        )
        monkeypatch.setitem(
            tool_package.TOOL_REGISTRY,
            "slack_post_update",
            lambda **_kwargs: {"success": True, "mode": "logged_locally"},
        )
        monkeypatch.setitem(
            tool_package.TOOL_REGISTRY,
            "postmortem_draft",
            lambda **_kwargs: {
                "success": True,
                "postmortem_path": str(tmp_path / "postmortems" / "fixture.md"),
            },
        )

        token = current_execution.set(ExecutionProgress(journal, EXECUTION_ID))
        try:
            comms = comms_module.publish_operational_outcome(
                comms_module.OperationalCommsFacts(
                    incident_id=INCIDENT_ID,
                    severity="P1",
                    verification=_healthy_verification(),
                    resolved=True,
                    approval_decision="approved",
                    executed_actions=(action_record,),
                    settling={"settled": True, "observations": [{"available_replicas": 1}]},
                )
            )
        finally:
            current_execution.reset(token)
        comms["inference"] = {
            "role": "comms",
            "trajectory": [],
            "final": {
                "incident_id": INCIDENT_ID,
                "summary": "The verifier evidence is ready for review.",
            },
        }
        final_record = {
            "source_sha": SOURCE_SHA,
            "certification_status": platform_ci.CERTIFICATION_STATUS,
            "env_resolved": True,
            "resolved": True,
            "triage": {"final": {"severity": "P1"}},
            "diagnosis": {"final": {"root_cause": "Frontend image could not be pulled."}},
            "remediation": {
                "final": {"outcome": "resolved", "executed_actions": [action_record]},
                "trajectory": [
                    {
                        "tool": "argocd_rollback",
                        "args": prepared["arguments"],
                        "output": action_output,
                    }
                ],
            },
            "comms": comms,
            "settling": {"settled": True, "observations": [{"available_replicas": 1}]},
            "approval": approval,
            "verification": _healthy_verification(),
        }
        journal.note(
            EXECUTION_ID,
            phase="finished",
            state="completed",
            event="incident_completed",
            patch=final_record,
        )
        audit.record("inc-other-fixture", "operator", "unrelated_event")
        response = client.get(
            f"/operations/{EXECUTION_ID}/evidence",
            headers={"X-AtlasOps-Key": API_KEY},
        )
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        return {
            "evidence": response.json(),
            "audit_secret": AUDIT_SECRET,
            "prepared_action": prepared,
        }


@pytest.fixture
def actual_completion_export(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    return _actual_operations_export(tmp_path, monkeypatch)


def _jaeger_response(
    *,
    incident_id: str,
    execution_id: str,
    now_microseconds: int,
) -> dict[str, object]:
    spans = [
        {
            "spanID": "incident-span",
            "processID": "coordinator",
            "operationName": "incident",
            "startTime": now_microseconds - 1_000,
            "tags": [
                {"key": "incident.id", "value": incident_id},
                {"key": "execution.id", "value": execution_id},
            ],
        },
        {
            "spanID": "webhook-span",
            "processID": "coordinator",
            "operationName": "http.request",
            "startTime": now_microseconds - 1_000,
            "tags": [
                {"key": "http.route", "value": "/webhook"},
                {"key": "http.response.status_code", "value": 200},
            ],
        },
    ]
    for index, role in enumerate(("triage", "diagnosis", "remediation", "comms"), start=1):
        spans.append(
            {
                "spanID": f"agent-span-{index}",
                "processID": "coordinator",
                "operationName": f"agent.{role}",
                "startTime": now_microseconds - 2_000 + index,
                "tags": [
                    {"key": "agent.role", "value": role},
                    {"key": "incident.id", "value": incident_id},
                ],
            }
        )
    for index, tool_name in enumerate(("slack_post_update", "postmortem_draft"), start=1):
        spans.append(
            {
                "spanID": f"comms-tool-span-{index}",
                "processID": "coordinator",
                "operationName": f"tool.{tool_name}",
                "startTime": now_microseconds - 500 + index,
                "tags": [
                    {"key": "agent.role", "value": "comms"},
                    {"key": "tool.name", "value": tool_name},
                    {"key": "incident.id", "value": incident_id},
                ],
            }
        )
    return {
        "data": [
            {
                "traceID": "trace-id",
                "processes": {
                    "coordinator": {
                        "serviceName": "atlasops-coordinator",
                        "tags": [{"key": "service.version", "value": SOURCE_SHA}],
                    }
                },
                "spans": spans,
            }
        ]
    }


def _validate_actual_completion(completion_api_export: dict[str, object]) -> dict[str, object]:
    return platform_ci.validate_completion_evidence(
        completion_api_export["evidence"],
        source_sha=SOURCE_SHA,
        history_id=HISTORY_ID,
        expected_operator=OPERATOR_ID,
        audit_secret=completion_api_export["audit_secret"],
    )


def _coordinator_telemetry_series(
    now: float,
    *,
    incident_labels: dict[str, str] | None = None,
    incident_timestamp: float | None = None,
    incident_value: str = "1",
) -> tuple[list[dict[str, object]], list[dict[str, object]], list[dict[str, object]]]:
    http_series = [
        {
            "metric": {
                "method": "POST",
                "route": "/webhook",
                "status": "200",
                "instance": "coordinator",
            },
            "value": [str(now), "2"],
        }
    ]
    incident_series = [
        {
            "metric": incident_labels
            or {"outcome": "resolved", "instance": "coordinator"},
            "value": [str(now if incident_timestamp is None else incident_timestamp), incident_value],
        }
    ]
    target_info = [
        {
            "metric": {
                "service_name": "atlasops-coordinator",
                "service_version": SOURCE_SHA,
                "instance": "coordinator",
            },
            "value": [str(now), "1"],
        }
    ]
    return http_series, incident_series, target_info


def test_coordinator_telemetry_reports_resolved_incident_counter_value() -> None:
    now = 1000.0
    http_series, incident_series, target_info = _coordinator_telemetry_series(now)

    metrics = platform_ci.validate_coordinator_telemetry(
        http_series,
        incident_series,
        target_info,
        source_sha=SOURCE_SHA,
        now=now,
    )

    assert metrics["http_sample"]["increase"] == 2.0
    assert metrics["incident_sample"]["counter"] == 1.0
    assert "increase" not in metrics["incident_sample"]


@pytest.mark.parametrize(
    ("incident_labels", "incident_timestamp", "incident_value"),
    [
        pytest.param(
            {"outcome": "unresolved", "instance": "coordinator"},
            1000.0,
            "1",
            id="wrong-outcome",
        ),
        pytest.param(
            {"outcome": "resolved", "instance": "coordinator"},
            699.0,
            "1",
            id="stale-counter",
        ),
        pytest.param(
            {"outcome": "resolved", "instance": "coordinator"},
            1000.0,
            "2",
            id="not-one-incident",
        ),
        pytest.param(
            {"outcome": "resolved", "instance": "other"},
            1000.0,
            "1",
            id="wrong-source-binding",
        ),
        pytest.param(
            {"outcome": "resolved", "instance": "coordinator"},
            1000.0,
            "malformed",
            id="malformed-counter",
        ),
    ],
)
def test_coordinator_telemetry_rejects_invalid_incident_counter(
    incident_labels: dict[str, str],
    incident_timestamp: float,
    incident_value: str,
) -> None:
    now = 1000.0
    http_series, incident_series, target_info = _coordinator_telemetry_series(
        now,
        incident_labels=incident_labels,
        incident_timestamp=incident_timestamp,
        incident_value=incident_value,
    )

    with pytest.raises(platform_ci.PlatformCIError):
        platform_ci.validate_coordinator_telemetry(
            http_series,
            incident_series,
            target_info,
            source_sha=SOURCE_SHA,
            now=now,
        )


def test_runner_polls_until_fresh_coordinator_metrics_bind_to_one_incident(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    actual_completion_export: dict[str, object],
) -> None:
    now = platform_ci.time.time()
    http_series, incident_series, target_info = _coordinator_telemetry_series(now)
    runner = platform_ci.Runner(_run_identity(), tmp_path / "telemetry-evidence", env={})
    runner.ports = {"jaeger": 16686}
    calls: list[str] = []
    completed_polls = 0
    clock = {"now": 0.0}
    trace_response = _jaeger_response(
        incident_id=INCIDENT_ID,
        execution_id=EXECUTION_ID,
        now_microseconds=int(now * 1_000_000),
    )
    records: list[tuple[str, dict[str, object]]] = []

    def query(query_text: str) -> list[dict[str, object]]:
        nonlocal completed_polls
        calls.append(query_text)
        if query_text == "increase(atlasops_http_requests_total[5m])":
            value = "0" if completed_polls < 2 else "1"
            return [{"metric": http_series[0]["metric"], "value": [str(now), value]}]
        if query_text == "atlasops_incidents_total":
            return [] if completed_polls == 0 else incident_series
        if query_text == "increase(atlasops_incidents_total[5m])":
            return [
                {
                    "metric": incident_series[0]["metric"],
                    "value": [str(now), "0"],
                }
            ]
        if query_text == "target_info":
            completed_polls += 1
            return target_info
        raise AssertionError(f"unexpected Prometheus query: {query_text}")

    monkeypatch.setattr(platform_ci.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        platform_ci.time,
        "sleep",
        lambda seconds: clock.update(now=clock["now"] + seconds),
    )
    runner._prometheus_query = query
    runner._http = lambda *_args, **_kwargs: (200, trace_response)
    runner._record = lambda key, value: records.append((key, value))

    result = runner._collect_coordinator_telemetry(
        {"incident_id": INCIDENT_ID, "execution_id": EXECUTION_ID},
        _validate_actual_completion(actual_completion_export),
    )

    assert completed_polls == 3
    assert clock["now"] == 10
    assert calls.count("increase(atlasops_http_requests_total[5m])") == 3
    assert calls.count("atlasops_incidents_total") == 3
    assert calls.count("target_info") == 3
    assert result["metrics"]["http_metric"] == "atlasops_http_requests_total"
    assert result["metrics"]["http_sample"]["increase"] == 1.0
    assert result["metrics"]["incident_metric"] == "atlasops_incidents_total"
    assert result["metrics"]["incident_sample"]["counter"] == 1.0
    assert result["trace"]["incident_span"]["execution_id"] == EXECUTION_ID
    assert records == [("coordinator_telemetry", result)]


def test_runner_coordinator_telemetry_poll_has_a_bounded_wait(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    now = platform_ci.time.time()
    http_series, incident_series, target_info = _coordinator_telemetry_series(now)
    runner = platform_ci.Runner(_run_identity(), tmp_path / "telemetry-timeout-evidence", env={})
    calls: list[str] = []
    clock = {"now": 0.0}

    def query(query_text: str) -> list[dict[str, object]]:
        calls.append(query_text)
        if query_text == "increase(atlasops_http_requests_total[5m])":
            return [{"metric": http_series[0]["metric"], "value": [str(now), "0"]}]
        if query_text == "atlasops_incidents_total":
            return incident_series
        if query_text == "increase(atlasops_incidents_total[5m])":
            return [{"metric": incident_series[0]["metric"], "value": [str(now), "0"]}]
        if query_text == "target_info":
            return target_info
        raise AssertionError(f"unexpected Prometheus query: {query_text}")

    monkeypatch.setattr(platform_ci.time, "monotonic", lambda: clock["now"])
    monkeypatch.setattr(
        platform_ci.time,
        "sleep",
        lambda seconds: clock.update(now=clock["now"] + seconds),
    )
    runner._prometheus_query = query

    with pytest.raises(platform_ci.PlatformCIError):
        runner._collect_coordinator_telemetry({}, {})

    assert clock["now"] == 90
    assert calls.count("atlasops_incidents_total") > 1


def test_completed_evidence_accepts_current_authenticated_operations_export(
    actual_completion_export: dict[str, object],
) -> None:
    evidence = actual_completion_export["evidence"]
    proof = _validate_actual_completion(actual_completion_export)

    assert proof["audit_scope"] == "incident_records"
    assert proof["execution_integrity"]["events"] == len(evidence["events"])
    assert len(proof["execution_progress_references"]) == len(evidence["events"])
    assert proof["audit_integrity"]["entries"] > len(proof["incident_audit_rows"])
    assert all(row["incident_id"] == INCIDENT_ID for row in proof["incident_audit_rows"])
    assert proof["approved_mutation"]["action_digest"] == evidence["record"]["approval"]["action_digest"]
    assert {
        (item["agent_role"], item["tool_name"])
        for item in proof["tool_audit_evidence"]
    } >= {
        ("triage", "alertmanager_list_alerts"),
        ("diagnosis", "argocd_app_history"),
        ("diagnosis", "kubectl_describe"),
        ("remediation", "argocd_rollback"),
        ("comms", "slack_post_update"),
        ("comms", "postmortem_draft"),
    }
    assert all(item["action_type"] == "tool_result" for item in proof["tool_audit_evidence"])
    assert any(row["action_type"] == "role_completed" for row in proof["incident_audit_rows"])
    assert any(row["action_type"] == "operator_decision" for row in proof["incident_audit_rows"])
    assert evidence["audit_scope"] == "incident_records"


@pytest.mark.parametrize(
    ("tamper", "message"),
    [
        ("missing_reference", "execution-progress audit reference"),
        ("signature", "audit row signature"),
        ("cross_incident", "outside the same incident"),
    ],
)
def test_completed_evidence_rejects_tampered_current_operations_export(
    actual_completion_export: dict[str, object],
    tamper: str,
    message: str,
) -> None:
    evidence = copy.deepcopy(actual_completion_export["evidence"])
    if tamper == "missing_reference":
        referenced = evidence["events"][0]["audit_entry_hash"]
        evidence["audit"] = [
            row for row in evidence["audit"] if row["entry_hash"] != referenced
        ]
    elif tamper == "signature":
        evidence["audit"][0]["signature"] = "0" * 64
    else:
        evidence["audit"][0]["incident_id"] = "inc-other"

    with pytest.raises(platform_ci.PlatformCIError, match=message):
        platform_ci.validate_completion_evidence(
            evidence,
            source_sha=SOURCE_SHA,
            history_id=HISTORY_ID,
            expected_operator=OPERATOR_ID,
            audit_secret=actual_completion_export["audit_secret"],
        )


def test_executed_actions_without_durable_mutation_pair_are_rejected(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    export = _actual_operations_export(
        tmp_path,
        monkeypatch,
        include_mutation_pair=False,
    )

    with pytest.raises(platform_ci.PlatformCIError, match="mutation start"):
        platform_ci.validate_completion_evidence(
            export["evidence"],
            source_sha=SOURCE_SHA,
            history_id=HISTORY_ID,
            expected_operator=OPERATOR_ID,
            audit_secret=export["audit_secret"],
        )


@pytest.mark.parametrize(
    ("corruption", "message"),
    [
        ({"mutation_started_count": 2}, "one prepared action, exact approval, and mutation start"),
        ({"mutation_returned_count": 0}, "one successful post-approval mutation return"),
        ({"mutation_returned_count": 2}, "one successful post-approval mutation return"),
        ({"mutation_return_success": False}, "durable mutation return"),
        ({"mutation_return_action_matches": False}, "durable mutation return"),
        ({"mutation_return_approval_matches": False}, "durable mutation return"),
    ],
)
def test_durable_mutation_pair_is_unique_and_bound_to_one_action(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    corruption: dict[str, object],
    message: str,
) -> None:
    export = _actual_operations_export(tmp_path, monkeypatch, **corruption)

    with pytest.raises(platform_ci.PlatformCIError, match=message):
        platform_ci.validate_completion_evidence(
            export["evidence"],
            source_sha=SOURCE_SHA,
            history_id=HISTORY_ID,
            expected_operator=OPERATOR_ID,
            audit_secret=export["audit_secret"],
        )


def test_coordinator_telemetry_requires_incident_roles_and_actual_comms_tool_spans(
    actual_completion_export: dict[str, object],
) -> None:
    proof = _validate_actual_completion(actual_completion_export)
    now_microseconds = 200_000_000
    trace_response = _jaeger_response(
        incident_id=INCIDENT_ID,
        execution_id=EXECUTION_ID,
        now_microseconds=now_microseconds,
    )

    trace = platform_ci.validate_coordinator_trace(
        trace_response,
        source_sha=SOURCE_SHA,
        incident_id=INCIDENT_ID,
        execution_id=EXECUTION_ID,
        tool_audit_evidence=proof["tool_audit_evidence"],
        now_microseconds=now_microseconds,
    )

    assert trace["incident_span"]["operation"] == "incident"
    assert trace["incident_span"]["execution_id"] == EXECUTION_ID
    assert set(trace["role_spans"]) == {"triage", "diagnosis", "remediation", "comms"}
    assert trace["http_request"]["route"] == "/webhook"
    assert set(trace["tool_spans"]) == {"slack_post_update", "postmortem_draft"}
    assert len(trace["tool_audit_evidence"]) >= 6

    http_only = copy.deepcopy(trace_response)
    http_only["data"][0]["spans"] = [http_only["data"][0]["spans"][1]]
    with pytest.raises(platform_ci.PlatformCIError, match="incident trace"):
        platform_ci.validate_coordinator_trace(
            http_only,
            source_sha=SOURCE_SHA,
            incident_id=INCIDENT_ID,
            execution_id=EXECUTION_ID,
            tool_audit_evidence=proof["tool_audit_evidence"],
            now_microseconds=now_microseconds,
        )

    wrong_execution = copy.deepcopy(trace_response)
    wrong_execution["data"][0]["spans"][0]["tags"][1]["value"] = "d" * 32
    with pytest.raises(platform_ci.PlatformCIError, match="incident trace"):
        platform_ci.validate_coordinator_trace(
            wrong_execution,
            source_sha=SOURCE_SHA,
            incident_id=INCIDENT_ID,
            execution_id=EXECUTION_ID,
            tool_audit_evidence=proof["tool_audit_evidence"],
            now_microseconds=now_microseconds,
        )

    wrong_source = copy.deepcopy(trace_response)
    wrong_source["data"][0]["processes"]["coordinator"]["tags"][0]["value"] = BASE_SHA
    with pytest.raises(platform_ci.PlatformCIError, match="incident trace"):
        platform_ci.validate_coordinator_trace(
            wrong_source,
            source_sha=SOURCE_SHA,
            incident_id=INCIDENT_ID,
            execution_id=EXECUTION_ID,
            tool_audit_evidence=proof["tool_audit_evidence"],
            now_microseconds=now_microseconds,
        )

    missing_comms_tool = copy.deepcopy(trace_response)
    missing_comms_tool["data"][0]["spans"] = [
        span
        for span in missing_comms_tool["data"][0]["spans"]
        if span.get("operationName") != "tool.postmortem_draft"
    ]
    with pytest.raises(platform_ci.PlatformCIError, match="incident trace"):
        platform_ci.validate_coordinator_trace(
            missing_comms_tool,
            source_sha=SOURCE_SHA,
            incident_id=INCIDENT_ID,
            execution_id=EXECUTION_ID,
            tool_audit_evidence=proof["tool_audit_evidence"],
            now_microseconds=now_microseconds,
        )

    with pytest.raises(platform_ci.PlatformCIError, match="role-bound tool-result"):
        platform_ci.validate_coordinator_trace(
            trace_response,
            source_sha=SOURCE_SHA,
            incident_id=INCIDENT_ID,
            execution_id=EXECUTION_ID,
            tool_audit_evidence=[],
            now_microseconds=now_microseconds,
        )


def test_wrong_digest_is_rejected_before_exact_operator_approval(tmp_path: Path, monkeypatch) -> None:
    runner = platform_ci.Runner(_run_identity(), tmp_path / "decision-evidence", env={})
    runner.ports = {"coordinator": 9099}
    secret_dir = tmp_path / "secrets"
    secret_dir.mkdir()
    api_key = "operations-fixture-key"
    (secret_dir / "atlasops-api-key.secret").write_text(api_key, encoding="utf-8")
    runner.secret_dir = secret_dir
    runner.secret_values.append(api_key)
    action_digest = "e" * 64
    approval_token = "apr-ExactActionToken"
    runner._pending_digest = action_digest
    runner.secret_values.append(approval_token)
    pending = {
        "incident_id": "inc-platform-ci",
        "token": approval_token,
        "action_digest": action_digest,
    }
    before = {
        "execution_id": "execution-platform-ci",
        "phase": "approval",
        "state": "running",
        "events": [],
    }
    runner._operations = lambda: {"pending": [pending]}
    runner._operation_evidence = lambda _incident_id: before
    runner._assert_workload_broken = lambda: True
    requests: list[tuple[str, dict[str, object]]] = []

    def http_double(url: str, **kwargs: object) -> tuple[int, dict[str, object]]:
        payload = kwargs["payload"]
        assert isinstance(payload, dict)
        requests.append((url, payload))
        if payload["action_digest"] != action_digest:
            return 400, {"error": "action_digest_mismatch"}
        return 200, {"ok": True, "decision": "approved"}

    monkeypatch.setattr(platform_ci, "http_json", http_double)

    runner._prove_rejection_before_approval(pending, before)
    runner._approve_exact_action(pending)

    assert len(requests) == 2
    assert all(url == "http://127.0.0.1:9099/api/operations/decision" for url, _ in requests)
    assert requests[0][1]["decision"] == "approved"
    assert requests[0][1]["action_digest"] == platform_ci.make_wrong_digest(action_digest)
    assert requests[1][1]["action_digest"] == action_digest
    assert all("approved_by" not in payload for _, payload in requests)
    assert runner.data["approval_request_shape"]["approved_by_body_field_sent"] is False


def _chat_tool(name: str) -> dict[str, object]:
    return {
        "type": "function",
        "function": {"name": name, "parameters": {"type": "object"}},
    }


class _FixtureRequest:
    def __init__(self, payload: dict[str, object] | None, authorization: str = ""):
        self.headers = {"authorization": authorization}
        self._payload = payload

    async def json(self) -> dict[str, object] | None:
        return self._payload


def _fixture_endpoint(app: object, path: str):
    route = next(
        item
        for item in app.routes
        if getattr(item, "path", None) == path and "POST" in getattr(item, "methods", set())
        or getattr(item, "path", None) == path and "GET" in getattr(item, "methods", set())
    )
    return route.endpoint


def _complete_immediate_request(coroutine):
    try:
        return coroutine.send(None)
    except StopIteration as completed:
        return completed.value


def test_fixture_requires_key_and_accepts_only_fixture_model() -> None:
    key = "fixture-ci-key-" + "k" * 40
    app = platform_ci.create_fixture_app(key, SOURCE_SHA)
    payload = {
        "model": platform_ci.FIXTURE_MODEL,
        "messages": [{"role": "system", "content": "You are the Triage Agent."}],
        "tools": [_chat_tool("alertmanager_list_alerts")],
    }
    models = _fixture_endpoint(app, "/v1/models")
    completions = _fixture_endpoint(app, "/v1/chat/completions")

    with pytest.raises(HTTPException) as missing_key:
        _complete_immediate_request(models(_FixtureRequest(None)))
    assert missing_key.value.status_code == 401

    model_list = _complete_immediate_request(
        models(_FixtureRequest(None, authorization=f"Bearer {key}"))
    )
    assert [item["id"] for item in model_list["data"]] == ["fixture/no-model"]

    with pytest.raises(HTTPException) as bad_key:
        _complete_immediate_request(
            completions(_FixtureRequest(payload, authorization="Bearer wrong-fixture-key"))
        )
    assert bad_key.value.status_code == 401

    wrong_model = dict(payload, model="Qwen/Qwen2.5-7B-Instruct")
    with pytest.raises(HTTPException) as rejected_model:
        _complete_immediate_request(
            completions(_FixtureRequest(wrong_model, authorization=f"Bearer {key}"))
        )
    assert rejected_model.value.status_code == 400


def test_fixture_derives_role_from_system_prompt_and_tool_schema() -> None:
    key = "fixture-ci-key-" + "r" * 40
    app = platform_ci.create_fixture_app(key, SOURCE_SHA)
    completions = _fixture_endpoint(app, "/v1/chat/completions")
    request = {
        "model": "fixture/no-model",
        "messages": [{"role": "system", "content": "You are the Triage Agent."}],
        "tools": [_chat_tool("alertmanager_list_alerts")],
        "role": "remediation",
    }

    response = _complete_immediate_request(
        completions(_FixtureRequest(request, authorization=f"Bearer {key}"))
    )

    message = response["choices"][0]["message"]
    assert message["tool_calls"][0]["function"]["name"] == "alertmanager_list_alerts"
    assert app.state.fixture_requests[0]["role"] == "triage"

    mismatch = dict(
        request,
        tools=[_chat_tool("argocd_app_history")],
    )
    with pytest.raises(HTTPException) as rejected:
        _complete_immediate_request(
            completions(_FixtureRequest(mismatch, authorization=f"Bearer {key}"))
        )
    assert rejected.value.status_code == 400


def test_fixture_requests_history_and_keeps_operational_comms_inference_only() -> None:
    key = "fixture-ci-key-" + "d" * 40
    app = platform_ci.create_fixture_app(key, SOURCE_SHA)
    completions = _fixture_endpoint(app, "/v1/chat/completions")
    headers = f"Bearer {key}"
    diagnosis = {
        "model": "fixture/no-model",
        "messages": [{"role": "system", "content": "You are the Diagnosis Agent."}],
        "tools": [
            _chat_tool("argocd_app_history"),
            _chat_tool("kubectl_describe"),
            _chat_tool("kubectl_get"),
        ],
    }
    comms_system = (
        "You are the Comms Agent. Review the attributed incident and verifier evidence. "
        "Return only JSON with incident_id and summary for internal review. "
        "You cannot publish messages or postmortems. The coordinator publishes from "
        "verified facts and records actual delivery separately."
    )
    comms = {
        "model": "fixture/no-model",
        "messages": [
            {"role": "system", "content": comms_system},
            {
                "role": "user",
                "content": json.dumps({"incident_id": INCIDENT_ID, "env_resolved": True}),
            },
        ],
    }

    diagnosis_response = _complete_immediate_request(
        completions(_FixtureRequest(diagnosis, authorization=headers))
    )
    comms_response = _complete_immediate_request(
        completions(_FixtureRequest(comms, authorization=headers))
    )

    diagnosis_tools = [
        call["function"]["name"]
        for call in diagnosis_response["choices"][0]["message"]["tool_calls"]
    ]
    assert diagnosis_response["choices"][0]["message"].get("role") == "assistant"
    assert comms_response["choices"][0]["message"].get("role") == "assistant"
    comms_message = comms_response["choices"][0]["message"]
    comms_result = json.loads(comms_message["content"])
    assert diagnosis_tools == ["argocd_app_history", "kubectl_describe", "kubectl_get"]
    assert "tool_calls" not in comms_message
    assert comms_response["choices"][0]["finish_reason"] == "stop"
    assert comms_result == {
        "incident_id": INCIDENT_ID,
        "summary": "Synthetic platform CI recovery evidence is ready for review.",
    }
    assert not {"slack_posted", "postmortem_path", "resolved"}.intersection(comms_result)
    assert app.state.fixture_requests[0]["role"] == "diagnosis"
    assert app.state.fixture_requests[1]["role"] == "comms"

    publication_tools = dict(comms, tools=[_chat_tool("slack_post_update")])
    with pytest.raises(HTTPException) as rejected_publication_tools:
        _complete_immediate_request(
            completions(_FixtureRequest(publication_tools, authorization=headers))
        )
    assert rejected_publication_tools.value.status_code == 400

    ambiguous_comms = dict(
        comms,
        messages=[{"role": "system", "content": "You are the Comms Agent."}],
    )
    with pytest.raises(HTTPException) as rejected_ambiguous_prompt:
        _complete_immediate_request(
            completions(_FixtureRequest(ambiguous_comms, authorization=headers))
        )
    assert rejected_ambiguous_prompt.value.status_code == 400


@pytest.mark.parametrize(
    (
        "target_labels",
        "target_reason",
        "target_pull_policy",
        "other_container_policy",
        "decoy_reason",
        "expected_status",
    ),
    [
        pytest.param(
            {"app": "frontend"},
            "ErrImageNeverPull",
            "Never",
            None,
            "CrashLoopBackOff",
            200,
            id="matching-pod-never-policy",
        ),
        pytest.param(
            {"app": "frontend"},
            "ErrImageNeverPull",
            "Always",
            "Never",
            None,
            400,
            id="matching-pod-wrong-policy",
        ),
        pytest.param(
            {"app": "frontend"},
            "CrashLoopBackOff",
            "Never",
            None,
            "ErrImageNeverPull",
            400,
            id="matching-pod-wrong-waiting-reason",
        ),
        pytest.param(
            {"app": "payments"},
            "ErrImageNeverPull",
            "Never",
            None,
            None,
            400,
            id="wrong-workload-pod",
        ),
    ],
)
def test_diagnosis_fixture_uses_observed_workload_scoped_pod_state(
    target_labels: dict[str, str],
    target_reason: str,
    target_pull_policy: str,
    other_container_policy: str | None,
    decoy_reason: str | None,
    expected_status: int,
) -> None:
    api_key = "fixture-ci-key-" + "p" * 40
    app = platform_ci.create_fixture_app(api_key, SOURCE_SHA)
    fault_image = "atlasops-ci-unavailable:run-318-2"
    history_output = {
        "success": True,
        "history": [{"id": 218, "revision": SOURCE_SHA}],
    }
    deployment_stdout = "\n".join(
        [
            "Name:                   frontend",
            "Namespace:              atlasops-boutique",
            "Selector:               app=frontend",
            "Replicas:               1 desired | 0 updated | 0 available | 1 unavailable",
            "Containers:",
            "  server:",
            f"    Image:              {fault_image}",
            "Events:",
            "  Type    Reason    Age   From                   Message",
            "  ----    ------    ---   ----                   -------",
            "  Normal  ScalingReplicaSet  1m   deployment-controller  Scaled up replica set frontend-x",
        ]
    )
    deployment_output = {
        "success": True,
        "stdout": deployment_stdout,
        "stderr": "",
        "returncode": 0,
    }

    def pod(
        name: str,
        labels: dict[str, str],
        reason: str,
        pull_policy: str,
        other_policy: str | None = None,
    ) -> dict[str, object]:
        containers = [
            {
                "name": "server",
                "image": fault_image,
                "imagePullPolicy": pull_policy,
            }
        ]
        if other_policy is not None:
            containers.append(
                {
                    "name": "sidecar",
                    "image": "sidecar:source",
                    "imagePullPolicy": other_policy,
                }
            )
        return {
            "metadata": {
                "name": name,
                "namespace": "atlasops-boutique",
                "labels": labels,
            },
            "spec": {"containers": containers},
            "status": {
                "containerStatuses": [
                    {
                        "name": "server",
                        "image": fault_image,
                        "state": {"waiting": {"reason": reason}},
                    }
                ]
            },
        }

    pod_items: list[dict[str, object]] = [
        pod(
            "frontend-abc",
            target_labels,
            target_reason,
            target_pull_policy,
            other_container_policy,
        )
    ]
    if decoy_reason is not None:
        pod_items.append(pod("payments-xyz", {"app": "payments"}, decoy_reason, "Never"))
    pods_output = {
        "success": True,
        "stdout": json.dumps({"items": pod_items}),
        "stderr": "",
        "returncode": 0,
        "parsed": {"items": pod_items},
    }
    history_call = {
        "id": "history-call",
        "type": "function",
        "function": {
            "name": "argocd_app_history",
            "arguments": json.dumps({"app": platform_ci.APP_NAME}),
        },
    }
    deployment_call = {
        "id": "deployment-call",
        "type": "function",
        "function": {
            "name": "kubectl_describe",
            "arguments": json.dumps(
                {
                    "resource": "deployment",
                    "name": "frontend",
                    "namespace": "atlasops-boutique",
                }
            ),
        },
    }
    pods_call = {
        "id": "pods-call",
        "type": "function",
        "function": {
            "name": "kubectl_get",
            "arguments": json.dumps(
                {
                    "resource": "pods",
                    "namespace": "atlasops-boutique",
                    "output": "json",
                }
            ),
        },
    }
    request = {
        "model": platform_ci.FIXTURE_MODEL,
        "messages": [
            {"role": "system", "content": "You are the Diagnosis Agent."},
            {
                "role": "user",
                "content": json.dumps(
                    {
                        "incident_id": INCIDENT_ID,
                        "observations": [],
                    }
                ),
            },
            {
                "role": "assistant",
                "tool_calls": [history_call, deployment_call, pods_call],
            },
            {
                "role": "tool",
                "tool_call_id": "history-call",
                "content": json.dumps(history_output),
            },
            {
                "role": "tool",
                "tool_call_id": "deployment-call",
                "content": json.dumps(deployment_output),
            },
            {
                "role": "tool",
                "tool_call_id": "pods-call",
                "content": json.dumps(pods_output),
            },
        ],
        "tools": [
            _chat_tool("argocd_app_history"),
            _chat_tool("kubectl_describe"),
            _chat_tool("kubectl_get"),
        ],
    }

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": f"Bearer {api_key}"},
            json=request,
        )

    assert response.status_code == expected_status
    if expected_status == 200:
        final = json.loads(response.json()["choices"][0]["message"]["content"])
        assert target_reason in final["root_cause"]
        assert fault_image in final["root_cause"]
        assert any(item["tool"] == "kubectl_get" for item in final["evidence"])
        assert any(
            item["tool"] == "kubectl_get" and "imagePullPolicy Never" in item["finding"]
            for item in final["evidence"]
        )
        assert all(
            "pull policy" not in item["finding"].lower()
            for item in final["evidence"]
            if item["tool"] == "kubectl_describe"
        )


@pytest.mark.parametrize("envelope", ["PodList", "List"])
def test_native_diagnosis_preserves_bounded_json_and_full_raw_pod_evidence(
    envelope: str,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from agents import coordinator
    import agents.tools.argocd as argocd_tools
    import agents.tools.kubectl as kubectl_tools

    fault_image = "atlasops-ci-unavailable:run-318-2"
    broken_pod = {
        "apiVersion": "v1",
        "kind": "Pod",
        "metadata": {
            "name": "frontend-abc",
            "namespace": platform_ci.APP_WORKLOAD_NAMESPACE,
            "uid": "frontend-pod-uid",
            "labels": {"app": "frontend", "password": "test-placeholder-pod-secret"},
            "managedFields": [{"fieldsV1": {"padding": "x" * 5000}}],
        },
        "spec": {
            "containers": [
                {"name": "server", "image": fault_image, "imagePullPolicy": "Never"}
            ]
        },
        "status": {
            "containerStatuses": [
                {
                    "name": "server",
                    "image": fault_image,
                    "state": {"waiting": {"reason": "ErrImageNeverPull"}},
                }
            ]
        },
    }
    pods = [
        {
            "apiVersion": "v1",
            "kind": "Pod",
            "metadata": {
                "name": f"healthy-{index}",
                "namespace": platform_ci.APP_WORKLOAD_NAMESPACE,
                "labels": {"app": f"healthy-{index}"},
            },
            "spec": {"containers": [{"name": "server", "image": "healthy:source"}]},
            "status": {
                "phase": "Running",
                "containerStatuses": [{"name": "server", "ready": True}],
            },
        }
        for index in range(8)
    ] + [broken_pod]
    raw_document = {"apiVersion": "v1", "kind": envelope, "items": pods}
    raw_stdout = json.dumps(raw_document)
    assert len(raw_stdout) > coordinator.TOOL_OUTPUT_MAX_CHARS

    def kubectl_read(command: list[str], **_kwargs: object) -> dict[str, object]:
        if command[1:3] == ["get", "pods"]:
            return {"success": True, "stdout": raw_stdout, "stderr": "", "returncode": 0}
        assert command[1:4] == ["describe", "deployment", "frontend"]
        return {
            "success": True,
            "stdout": f"Name: frontend\nNamespace: atlasops-boutique\nContainers:\n  server:\n    Image: {fault_image}\n",
            "stderr": "",
            "returncode": 0,
        }

    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    monkeypatch.setenv("ATLASOPS_WORKLOAD_NAMESPACE", platform_ci.APP_WORKLOAD_NAMESPACE)
    monkeypatch.setenv("ATLASOPS_ARGOCD_APP", platform_ci.APP_NAME)
    monkeypatch.setattr(kubectl_tools, "_run", kubectl_read)
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "kubectl_get", kubectl_tools.kubectl_get)
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "kubectl_describe", kubectl_tools.kubectl_describe)
    application = _pre_fault_state()[0]
    application["metadata"]["managedFields"] = [{"fieldsV1": {"padding": "x" * 5000}}]

    def argo_read(method: str, path: str, **_kwargs: object) -> dict[str, object]:
        assert (method, path) == ("GET", f"/applications/{platform_ci.APP_NAME}")
        return {"success": True, "data": copy.deepcopy(application)}

    monkeypatch.setattr(argocd_tools, "_api", argo_read)
    monkeypatch.setitem(coordinator.TOOL_REGISTRY, "argocd_app_history", argocd_tools.argocd_app_history)
    for name in ("require_audit_log", "audit_log", "thought_emit", "circuit_breaker"):
        monkeypatch.setattr(coordinator, name, MagicMock())
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path / "trajectories")
    monkeypatch.setattr(coordinator, "MODEL_NAME", platform_ci.FIXTURE_MODEL)
    monkeypatch.setattr(coordinator, "VLLM_BASE", "http://fixture.invalid/v1")
    api_key = "test-placeholder-native-fixture-key"
    monkeypatch.setattr(coordinator, "API_KEY", api_key)
    model_messages: list[str] = []
    app = platform_ci.create_fixture_app(api_key, SOURCE_SHA)

    with TestClient(app) as client:
        async def complete(native_client, _url, payload, **_kwargs):
            for message in payload["messages"]:
                if message.get("role") != "tool":
                    continue
                json.loads(message["content"])
                assert len(message["content"]) <= coordinator.TOOL_OUTPUT_MAX_CHARS
                model_messages.append(message["content"])
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": native_client.headers["Authorization"]},
                json=payload,
            )
            response.raise_for_status()
            return response

        monkeypatch.setattr(coordinator, "post_with_retry", complete)
        result = asyncio.run(
            coordinator.call_agent(
                "diagnosis",
                {"incident_id": INCIDENT_ID, "observations": []},
                max_turns=2,
            )
        )

    assert "ErrImageNeverPull" in result["final"]["root_cause"]
    assert fault_image in result["final"]["root_cause"]
    pod_observation = next(row for row in result["trajectory"] if row.get("tool") == "kubectl_get")
    assert pod_observation["output"]["stdout"] == raw_stdout
    assert pod_observation["output"]["parsed"] == raw_document
    history_observation = next(
        row for row in result["trajectory"] if row.get("tool") == "argocd_app_history"
    )
    assert history_observation["output"]["data"] == application
    assert history_observation["output"]["history"][0]["id"] == 218
    assert "ErrImageNeverPull" in "".join(model_messages)
    assert "frontend-pod-uid" in "".join(model_messages)
    assert "test-placeholder-pod-secret" not in "".join(model_messages)
    projected_message = next(
        json.loads(message)
        for message in model_messages
        if isinstance(json.loads(message).get("parsed"), dict)
    )
    assert projected_message["truncated"] is True
    assert projected_message["omitted_items"] > 0
    projected_pod = projected_message["parsed"]["items"][0]
    assert projected_pod["spec"]["containers"][0]["imagePullPolicy"] == "Never"
    assert projected_pod["status"]["containerStatuses"][0]["state"]["waiting"]["reason"] == "ErrImageNeverPull"


def test_bounded_pod_evidence_prioritizes_pending_pods_without_container_statuses() -> None:
    from agents import coordinator

    healthy_pods = [
        {
            "metadata": {
                "name": f"healthy-{index}",
                "namespace": platform_ci.APP_WORKLOAD_NAMESPACE,
                "labels": {"app": f"healthy-{index}"},
            },
            "spec": {"containers": [{"name": "server", "image": "healthy:source"}]},
            "status": {
                "phase": "Running",
                "containerStatuses": [{"name": "server", "ready": True}],
            },
        }
        for index in range(8)
    ]
    pending_pod = {
        "metadata": {
            "name": "frontend-pending",
            "namespace": platform_ci.APP_WORKLOAD_NAMESPACE,
            "uid": "frontend-pending-pod-uid",
            "labels": {"app": "frontend"},
        },
        "spec": {
            "containers": [
                {
                    "name": "server",
                    "image": "atlasops-ci-unavailable:run-318-2",
                    "imagePullPolicy": "Never",
                }
            ]
        },
        "status": {"phase": "Pending", "containerStatuses": []},
    }
    document = {"kind": "PodList", "items": healthy_pods + [pending_pod]}
    output = {"success": True, "stdout": json.dumps(document), "parsed": document}
    original = copy.deepcopy(output)

    message = coordinator._model_tool_message("kubectl_get", output)
    visible = json.loads(message)

    assert len(message) <= coordinator.TOOL_OUTPUT_MAX_CHARS
    assert visible["omitted_items"] > 0
    assert visible["parsed"]["items"][0] == pending_pod
    assert output == original


@pytest.mark.parametrize("success", [False, True])
def test_oversized_tool_text_remains_valid_json_with_accurate_verifier_attribution(
    success: bool,
) -> None:
    from agents import coordinator

    output = {
        "success": success,
        "stdout": 'Observed text with escapes "\\\n' * 1000,
        "api_key": "test-placeholder-text-secret",
        "verifier_observation": {
            "env_resolved": False,
            "verification_status": "failed",
            "observation_type": "authoritative_environment_verifier",
        },
    }
    original = copy.deepcopy(output)
    message = coordinator._model_tool_message("kubectl_logs", output)
    visible = json.loads(message)
    assert len(message) <= coordinator.TOOL_OUTPUT_MAX_CHARS
    assert "test-placeholder-text-secret" not in message
    assert visible["success"] is success
    assert visible["truncated"] is True
    assert visible["verifier_observation"] == output["verifier_observation"]
    assert output == original


def test_fixture_emits_credential_safe_jsonl_consumed_by_runner_logs(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    api_key = "fixture-ci-key-" + "s" * 40
    body_marker = "fixture-private-prompt-body"
    app = platform_ci.create_fixture_app(api_key, SOURCE_SHA)
    history_observation = {
        "tool": "argocd_app_history",
        "args": {"app": platform_ci.APP_NAME},
        "output": {
            "success": True,
            "history": [{"id": int(HISTORY_ID), "revision": SOURCE_SHA}],
        },
    }
    diagnosis_tools = [
        _chat_tool("argocd_app_history"),
        _chat_tool("kubectl_describe"),
        _chat_tool("kubectl_get"),
    ]
    comms_system = (
        "You are the Comms Agent. Review the attributed incident and verifier evidence. "
        "Return only JSON with incident_id and summary for internal review. "
        "You cannot publish messages or postmortems. The coordinator publishes from "
        "verified facts and records actual delivery separately."
    )
    requests = [
        {
            "model": platform_ci.FIXTURE_MODEL,
            "messages": [
                {"role": "system", "content": "You are the Triage Agent."},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"incident_id": INCIDENT_ID, "prompt": body_marker}
                    ),
                },
            ],
            "tools": [_chat_tool("alertmanager_list_alerts")],
        },
        {
            "model": platform_ci.FIXTURE_MODEL,
            "messages": [
                {"role": "system", "content": "You are the Diagnosis Agent."},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"incident_id": INCIDENT_ID, "prompt": body_marker}
                    ),
                },
            ],
            "tools": diagnosis_tools,
        },
        {
            "model": platform_ci.FIXTURE_MODEL,
            "messages": [
                {"role": "system", "content": "You are the Remediation Agent."},
                {
                    "role": "user",
                    "content": json.dumps(
                        {
                            "incident_id": INCIDENT_ID,
                            "prompt": body_marker,
                                "diagnosis_observations": [history_observation],
                        }
                    ),
                },
            ],
            "tools": [_chat_tool("argocd_rollback")],
        },
        {
            "model": platform_ci.FIXTURE_MODEL,
            "messages": [
                {"role": "system", "content": comms_system},
                {
                    "role": "user",
                    "content": json.dumps(
                        {"incident_id": INCIDENT_ID, "prompt": body_marker}
                    ),
                },
            ],
        },
    ]

    with TestClient(app) as client:
        responses = [
            client.post(
                "/v1/chat/completions",
                headers={"Authorization": f"Bearer {api_key}"},
                json=payload,
            )
            for payload in requests
        ]

    assert [response.status_code for response in responses] == [200, 200, 200, 200]
    output = capsys.readouterr().out
    assert api_key not in output
    assert body_marker not in output
    rows = [json.loads(line) for line in output.splitlines() if line.strip()]
    assert rows == app.state.fixture_requests
    assert {row["role"] for row in rows} == {"triage", "diagnosis", "remediation", "comms"}
    assert any(
        row["role"] == "remediation" and row["selected_history_id"] == HISTORY_ID
        for row in rows
    )
    assert all("authorization" not in row and "messages" not in row for row in rows)

    runner = platform_ci.Runner(_run_identity(), tmp_path / "fixture-log-evidence", env={})
    runner.data["pre_fault_eligibility"] = {"history_id": HISTORY_ID}
    records: list[tuple[str, dict[str, object]]] = []
    runner._read_pod_logs = lambda *_args: output
    runner._record = lambda key, value: records.append((key, value))
    runner._collect_fixture_logs()

    assert records[0][0] == "fixture_requests"
    assert records[0][1]["roles"] == ["comms", "diagnosis", "remediation", "triage"]
    assert records[0][1]["raw_authorization_headers_archived"] is False


def test_remediation_fixture_uses_only_observed_argo_history_id() -> None:
    key = "fixture-ci-key-" + "h" * 40
    app = platform_ci.create_fixture_app(key, SOURCE_SHA)
    completions = _fixture_endpoint(app, "/v1/chat/completions")
    history_observation = {
        "tool": "argocd_app_history",
        "args": {"app": platform_ci.APP_NAME},
        "output": {
            "success": True,
            "history": [{"id": 218, "revision": SOURCE_SHA}],
        },
    }
    request = {
        "model": "fixture/no-model",
        "messages": [
            {"role": "system", "content": "You are the Remediation Agent."},
            {
                "role": "user",
                "content": json.dumps(
                    {"incident_id": "inc-platform-ci", "diagnosis_observations": [history_observation]}
                ),
            },
        ],
        "tools": [_chat_tool("argocd_rollback")],
    }

    response = _complete_immediate_request(
        completions(_FixtureRequest(request, authorization=f"Bearer {key}"))
    )

    arguments = json.loads(
        response["choices"][0]["message"]["tool_calls"][0]["function"]["arguments"]
    )
    assert arguments == {"app": platform_ci.APP_NAME, "revision": "218"}
    assert app.state.fixture_requests[0]["selected_history_id"] == "218"

    no_history = dict(request, messages=request["messages"][:1])
    with pytest.raises(HTTPException) as rejected:
        _complete_immediate_request(
            completions(_FixtureRequest(no_history, authorization=f"Bearer {key}"))
        )
    assert rejected.value.status_code == 400
    fabricated_history = dict(
        request,
        messages=[
            request["messages"][0],
            {"role": "user", "content": json.dumps({"observations": [history_observation]})},
        ],
    )
    with pytest.raises(HTTPException) as rejected_fabrication:
        _complete_immediate_request(
            completions(_FixtureRequest(fabricated_history, authorization=f"Bearer {key}"))
        )
    assert rejected_fabrication.value.status_code == 400

    application, deployment, rule = _pre_fault_state()
    with pytest.raises(platform_ci.PlatformCIError, match="availability rule is not loaded"):
        platform_ci.validate_pre_fault(
            application,
            deployment,
            None,
            [],
            source_sha=SOURCE_SHA,
        )


@pytest.mark.parametrize(
    ("role", "tool"),
    [
        ("triage", "alertmanager_list_alerts"),
        ("diagnosis", "argocd_app_history"),
        ("remediation", "argocd_rollback"),
    ],
)
def test_fixture_identifies_role_from_complete_production_prompt(role: str, tool: str) -> None:
    from agents.coordinator import load_prompt

    payload = {
        "messages": [{"role": "system", "content": load_prompt(role)}],
        "tools": [_chat_tool(tool)],
    }
    assert platform_ci.derive_fixture_role(payload) == role
    payload["tools"] = [_chat_tool("argocd_rollback" if role != "remediation" else "argocd_app_history")]
    with pytest.raises(platform_ci.PlatformCIError, match="consistent role"):
        platform_ci.derive_fixture_role(payload)


def test_boutique_traffic_preserves_cookie_through_product_cart_and_checkout() -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    requests: list[tuple[str, str | None]] = []

    class BoutiqueHandler(BaseHTTPRequestHandler):
        def _respond(self) -> None:
            requests.append((self.path, self.headers.get("Cookie")))
            if self.command == "POST":
                self.rfile.read(int(self.headers.get("Content-Length", "0")))
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            if self.path == "/":
                self.send_header("Set-Cookie", "shop_session-id=fixture-session; Path=/")
                body = '<a href="/product/fixture-product">Product</a>'
            elif self.path == "/product/fixture-product":
                body = '<form action="/cart" method="POST"><input name="quantity" value="1"></form>'
            elif self.path == "/cart":
                body = '<form action="/cart/checkout" method="POST"><input name="email" value=""></form>'
            else:
                body = "Order confirmed"
            self.end_headers()
            self.wfile.write(body.encode())

        do_GET = _respond
        do_POST = _respond

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), BoutiqueHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        result = platform_ci.exercise_boutique(f"http://127.0.0.1:{server.server_port}")
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert result["checkout_status"] == "completed"
    assert result["synthetic_checkout"] is True
    assert result["request_paths"] == ["/", "/product/fixture-product", "/cart", "/cart", "/cart/checkout"]
    assert requests[0] == ("/", None)
    assert all(cookie == "shop_session-id=fixture-session" for _path, cookie in requests[1:])


def test_pending_admission_does_not_require_a_completed_webhook_response(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from datetime import datetime, timezone
    from unittest.mock import Mock

    runner = platform_ci.Runner(_run_identity(), tmp_path / "pending-admission", env={})
    before = {"metadata": {"uid": "frontend-uid"}}
    pending = {"incident_id": INCIDENT_ID}
    evidence = {"execution_id": EXECUTION_ID, "events": [{"event": "approval_requested"}]}
    prepared = {"tool": "argocd_rollback", "arguments": {}, "target": {}, "verification_target": {}, "action_digest": "d" * 64}
    runner.data["pre_fault_eligibility"] = {}
    runner._fault_deployment = lambda: (before, "fixture-image", datetime.now(timezone.utc))
    runner._wait_for_broken_pod = lambda *_args: None
    runner._wait_for_pending_incident = lambda *_args: (pending, evidence)
    runner._pending_action = lambda *_args: prepared
    runner._alert_rule_firing = lambda: {"firing": True}
    runner._alertmanager_alerts = lambda: []
    runner._active_frontend_alert = lambda _alerts: {"active": True}
    completed_response = Mock(side_effect=AssertionError("webhook is still waiting for approval"))
    monkeypatch.setattr(runner, "_verify_webhook_admission_log", completed_response)

    assert runner._fault_and_wait_for_admission() == (pending, evidence, prepared, before)
    assert completed_response.call_count == 0
    webhook = runner.data["alert_admission"]["webhook"]
    assert webhook["authenticated_admission"] is True
    assert webhook["http_status"] is None
    assert webhook["execution_id"] == EXECUTION_ID


def test_large_execution_export_is_preserved_and_verified_through_http(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    exported = _actual_operations_export(
        tmp_path, monkeypatch,
        initial_alert={"raw_observation": {"metadata": {"annotations": {"fixture_padding": "x" * 20_000}}}},
    )
    raw = json.dumps(exported["evidence"]).encode()
    assert len(raw) > platform_ci.MAX_EVIDENCE_TEXT

    class EvidenceHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            assert self.headers.get("X-AtlasOps-Key") == API_KEY
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), EvidenceHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    runner = platform_ci.Runner(_run_identity(), tmp_path / "large-export", env={})
    runner._api_root = lambda: f"http://127.0.0.1:{server.server_port}"
    runner._operations_headers = lambda: {"X-AtlasOps-Key": API_KEY}
    try:
        with pytest.raises(platform_ci.PlatformCIError, match="bounded evidence size"):
            platform_ci.http_json(
                f"{runner._api_root()}/api/operations/{EXECUTION_ID}/evidence",
                headers={"X-AtlasOps-Key": API_KEY},
            )
        result = runner._operations_by_id(EXECUTION_ID)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    assert result == exported["evidence"]
    proof = _validate_actual_completion({**exported, "evidence": result})
    assert proof["execution_id"] == EXECUTION_ID
    assert proof["execution_integrity"]["events"] == len(result["events"])


@pytest.mark.parametrize(
    "mismatch",
    ["none", "missing-marker", "other-operation", "bad-receipt", "bad-digest", "failed-result", "old-version"],
)
def test_same_second_sync_is_bound_to_the_successful_exact_patch_receipt(
    mismatch: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch, before, application = _receipt_bound_sync()
    if mismatch == "missing-marker":
        application["status"]["operationState"]["operation"].pop("info")
    elif mismatch == "other-operation":
        application["status"]["operationState"]["operation"]["info"][0]["value"] = "e" * 64
    elif mismatch == "bad-receipt":
        dispatch["action_result"]["mutation_receipt"]["operation"]["info"][0]["value"] = "e" * 64
    elif mismatch == "bad-digest":
        dispatch["action_digest"] = "0" * 64
    elif mismatch == "failed-result":
        dispatch["action_result"]["success"] = False
    elif mismatch == "old-version":
        application["metadata"]["resourceVersion"] = before["metadata"]["resourceVersion"]
    runner = platform_ci.Runner(_run_identity(), tmp_path / "sync-receipt", env={})
    runner._application = lambda: application
    ticks = iter((0.0, 1.0, 301.0))
    monkeypatch.setattr(platform_ci.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(platform_ci.time, "sleep", lambda _seconds: None)
    if mismatch == "none":
        assert runner._wait_for_application_sync(SOURCE_SHA, dispatch, before=before) is application
    else:
        with pytest.raises(platform_ci.PlatformCIError):
            runner._wait_for_application_sync(SOURCE_SHA, dispatch, before=before)


def test_downstream_observations_are_bounded_without_changing_raw_input(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from unittest.mock import MagicMock

    from agents import coordinator

    raw_history = {
        "success": True,
        "data": {"metadata": {"name": platform_ci.APP_NAME, "padding": "x" * 5000}},
        "history": [{"id": 218, "revision": SOURCE_SHA}],
    }
    user_input = {
        "incident_id": INCIDENT_ID,
        "diagnosis_observations": [{
            "tool": "argocd_app_history", "args": {"app": platform_ci.APP_NAME},
            "output": raw_history,
        }],
    }
    original = copy.deepcopy(user_input)
    for name in ("require_audit_log", "audit_log", "thought_emit", "circuit_breaker"):
        monkeypatch.setattr(coordinator, name, MagicMock())
    monkeypatch.setenv("ATLASOPS_OPERATIONAL_MODE", "1")
    monkeypatch.setattr(coordinator, "TRAJECTORIES_DIR", tmp_path / "trajectories")
    monkeypatch.setattr(coordinator, "MODEL_NAME", platform_ci.FIXTURE_MODEL)
    monkeypatch.setattr(coordinator, "VLLM_BASE", "http://fixture.invalid/v1")
    key = "test-placeholder-downstream-fixture-key"
    monkeypatch.setattr(coordinator, "API_KEY", key)
    app = platform_ci.create_fixture_app(key, SOURCE_SHA)
    with TestClient(app) as client:
        async def complete(native_client, _url, payload, **_kwargs):
            observed = platform_ci._user_input(payload["messages"])["diagnosis_observations"][0]
            assert observed["tool"] == "argocd_app_history"
            assert observed["args"] == {"app": platform_ci.APP_NAME}
            assert observed["output"]["history"] == [{"id": 218, "revision": SOURCE_SHA}]
            assert len(json.dumps(observed["output"])) <= coordinator.TOOL_OUTPUT_MAX_CHARS
            response = client.post(
                "/v1/chat/completions",
                headers={"Authorization": native_client.headers["Authorization"]},
                json=payload,
            )
            response.raise_for_status()
            return response

        monkeypatch.setattr(coordinator, "post_with_retry", complete)
        result = asyncio.run(coordinator.call_agent("comms", user_input, max_turns=1))
    assert result["final"]["incident_id"] == INCIDENT_ID
    assert user_input == original


def test_generic_kubectl_list_projection_excludes_non_pod_objects() -> None:
    from agents import coordinator

    document = {
        "apiVersion": "v1", "kind": "List",
        "items": [
            {"apiVersion": "apps/v1", "kind": "Deployment", "metadata": {
                "name": "not-a-pod", "padding": "x" * 5000,
            }},
            {"apiVersion": "v1", "kind": "Pod", "metadata": {
                "name": "frontend-pod", "namespace": platform_ci.APP_WORKLOAD_NAMESPACE,
            }, "spec": {"containers": []}, "status": {}},
        ],
    }
    result = json.loads(coordinator._model_tool_message(
        "kubectl_get", {"success": True, "parsed": document},
    ))
    assert [pod["metadata"]["name"] for pod in result["parsed"]["items"]] == ["frontend-pod"]
    assert result["omitted_items"] == 1


@pytest.mark.parametrize("size", [127, 128, 129])
def test_http_reader_rejects_cap_plus_one_without_truncating_json(size: int) -> None:
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    raw = json.dumps("x" * (size - 2)).encode()

    class BoundedHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, _format: str, *_args: object) -> None:
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), BoundedHandler)
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/evidence"
        if size > 128:
            with pytest.raises(platform_ci.PlatformCIError, match="bounded evidence size"):
                platform_ci.http_json(url, max_bytes=128)
        else:
            assert platform_ci.http_json(url, max_bytes=128) == (200, "x" * (size - 2))
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
