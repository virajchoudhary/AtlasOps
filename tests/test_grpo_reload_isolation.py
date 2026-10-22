"""Model-free reload-isolation contracts; never probe the real Internet in CI."""

import hashlib
import json
import os
import socket
import subprocess
import sys
from datetime import UTC, datetime

import pytest

from training import grpo_reload_isolation as isolation


def receipt(tmp_path, **changes):
    value = {
        "reload_profile": isolation.KAGGLE_PROFILE,
        "source_sha": "a" * 40,
        "observed_at": datetime.now(UTC).isoformat(),
        "kaggle_internet_enabled": False,
        "private_context": True,
        "operator": "Viraj Choudhary",
        "launcher_pid": os.getppid(),
    }
    value.update(changes)
    path = tmp_path / "receipt.json"
    path.write_text(json.dumps(value))
    return path, hashlib.sha256(path.read_bytes()).hexdigest()


def failed_probes():
    return [
        {"target": host, "port": port, "connected": False, "error_type": "TimeoutError",
         "timeout_seconds": isolation.PROBE_TIMEOUT_SECONDS}
        for host, port in isolation.PROBE_TARGETS
    ]


def test_offline_profile_requires_receipt_and_actual_egress_failure(tmp_path, monkeypatch):
    path, sha = receipt(tmp_path)
    monkeypatch.setattr(isolation.LoopbackGuard, "install", lambda self: None)
    monkeypatch.setattr(isolation.LoopbackGuard, "install_syscall_guard", lambda self, **kwargs: None)
    monkeypatch.setattr(isolation, "outbound_probes", failed_probes)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    evidence, guard = isolation.prepare_isolation(
        isolation.KAGGLE_PROFILE, receipt_path=path, receipt_sha256=sha, source_sha="a" * 40
    )
    evidence["syscall_guard_installed"] = True
    assert isolation.validate_isolation_evidence(evidence)
    assert guard.attempts == []
    monkeypatch.setattr(isolation, "outbound_probes", lambda: [
        {**r, "connected": True} for r in failed_probes()
    ])
    with pytest.raises(PermissionError, match="egress"):
        isolation.prepare_isolation(
            isolation.KAGGLE_PROFILE, receipt_path=path, receipt_sha256=sha, source_sha="a" * 40
        )


@pytest.mark.parametrize("changes", [
    {"kaggle_internet_enabled": True}, {"private_context": False},
    {"launcher_pid": -1}, {"source_sha": "b" * 40},
])
def test_context_refused_before_probes(tmp_path, monkeypatch, changes):
    path, sha = receipt(tmp_path, **changes)
    monkeypatch.setattr(isolation, "outbound_probes", lambda: pytest.fail("probe called"))
    with pytest.raises(ValueError):
        isolation.prepare_isolation(
            isolation.KAGGLE_PROFILE, receipt_path=path, receipt_sha256=sha, source_sha="a" * 40
        )


@pytest.mark.parametrize("address", [
    ("1.1.1.1", 443), ("::ffff:1.1.1.1", 443), ("example.com", 443),
])
def test_guard_latches_forbidden_socket_events(address):
    guard = isolation.LoopbackGuard()
    with pytest.raises(PermissionError):
        guard.audit("socket.connect", (None, address))
    with pytest.raises(PermissionError):
        guard.require_clean()


def test_guard_allows_literal_loopback_only():
    guard = isolation.LoopbackGuard()
    guard.check_address("socket.connect", ("127.0.0.1", 123))
    guard.check_address("socket.connect", ("::1", 123))
    guard.require_clean()


def test_installed_process_guard_covers_real_connect_ex_and_lookup(tmp_path):
    # Dedicated fresh subprocess: permanent audit hooks cannot be removed.
    code = """
import socket
from training.grpo_reload_isolation import LoopbackGuard
g=LoopbackGuard();g.install()
for f in (
 lambda: socket.socket().connect_ex(('1.1.1.1',443)),
 lambda: socket.socket().connect(('1.1.1.1',443)),
 lambda: socket.getaddrinfo('huggingface.co',443),
 lambda: socket.socket(socket.AF_INET,socket.SOCK_DGRAM).sendto(b'x',('8.8.8.8',53)),
):
 try:f()
 except PermissionError:pass
 else:raise AssertionError('external operation was not blocked')
assert len(g.attempts)==4
print('GUARD_PASS')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert "GUARD_PASS" in result.stdout


def test_probes_use_direct_addresses_timeouts_and_close(monkeypatch):
    created = []

    class Probe:
        def __init__(self, *args):
            created.append(self)
            self.timeout = None
            self.closed = False

        def __enter__(self):
            return self

        def __exit__(self, *args):
            self.closed = True

        def settimeout(self, seconds):
            self.timeout = seconds

        def connect(self, address):
            assert address in isolation.PROBE_TARGETS
            raise TimeoutError()

    monkeypatch.setattr(socket, "socket", Probe)
    assert len(isolation.outbound_probes()) == 3
    assert all(p.closed and p.timeout == 2 for p in created)


def test_acceptance_rejects_nonempty_guard_latch(tmp_path, monkeypatch):
    path, sha = receipt(tmp_path)
    monkeypatch.setattr(isolation.LoopbackGuard, "install", lambda self: None)
    monkeypatch.setattr(isolation.LoopbackGuard, "install_syscall_guard", lambda self, **kwargs: None)
    monkeypatch.setattr(isolation, "outbound_probes", failed_probes)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    evidence, _ = isolation.prepare_isolation(
        isolation.KAGGLE_PROFILE, receipt_path=path, receipt_sha256=sha, source_sha="a" * 40
    )
    evidence["forbidden_attempts"] = [{"operation": "socket.connect", "outcome": "blocked"}]
    assert not isolation.validate_isolation_evidence(evidence)


@pytest.mark.parametrize("timeout", [None, 0.5, 30.0])
def test_acceptance_requires_the_fixed_probe_timeout(tmp_path, monkeypatch, timeout):
    # Codex finding (PR181): probe records with a missing or arbitrary timeout were accepted.
    path, sha = receipt(tmp_path)
    monkeypatch.setattr(isolation.LoopbackGuard, "install", lambda self: None)
    monkeypatch.setattr(isolation.LoopbackGuard, "install_syscall_guard", lambda self, **kwargs: None)
    monkeypatch.setattr(isolation, "outbound_probes", failed_probes)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    evidence, _ = isolation.prepare_isolation(
        isolation.KAGGLE_PROFILE, receipt_path=path, receipt_sha256=sha, source_sha="a" * 40
    )
    evidence["syscall_guard_installed"] = True
    assert isolation.validate_isolation_evidence(evidence)
    probe = evidence["outbound_probes"][0]
    if timeout is None:
        probe.pop("timeout_seconds")
    else:
        probe["timeout_seconds"] = timeout
    assert not isolation.validate_isolation_evidence(evidence)


@pytest.mark.parametrize("error", ["ConnectionRefusedError", "ConnectionResetError", "OSError"])
def test_remote_or_unknown_probe_errors_never_prove_isolation(error):
    assert not isolation.probe_proves_blocked({"connected": False, "error_type": error})


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only native syscall guard")
def test_native_socket_syscall_is_trapped_before_network():
    code = """
import ctypes
from training.grpo_reload_isolation import LoopbackGuard
g=LoopbackGuard();g.install();g.install_syscall_guard()
try:ctypes.CDLL(None).syscall(42,-1,0,0)
except PermissionError:pass
else:raise AssertionError('native connect bypass')
assert g.attempts
print('NATIVE_GUARD_PASS')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only descriptor check")
def test_preconnected_local_socket_refuses_guard_install():
    code = """
import socket
from training.grpo_reload_isolation import LoopbackGuard
a,b=socket.socketpair()
try:LoopbackGuard().install_syscall_guard()
except PermissionError as e:assert 'socket descriptors' in str(e)
else:raise AssertionError('connected socket was admitted')
a.close();b.close()
print('INHERITED_SOCKET_REFUSED')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=10)
    assert result.returncode == 0, result.stderr


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only GPU socket classes")
def test_gpu_discovery_audit_rejects_ip_and_subprocesses():
    guard = isolation.LoopbackGuard()
    guard.gpu_discovery = True
    guard.audit("socket.__new__", (None, socket.AF_UNIX, socket.SOCK_SEQPACKET, 0))
    for event, args in (
        ("socket.__new__", (None, socket.AF_INET, socket.SOCK_STREAM, 0)),
        ("socket.connect", (None, "/tmp/nvidia-mps/control")),
        ("subprocess.Popen", ("/sbin/ldconfig", ["/sbin/ldconfig", "-p"], None, None)),
    ):
        with pytest.raises(PermissionError):
            guard.audit(event, args)
    assert len(guard.attempts) == 3


@pytest.mark.skipif(sys.platform != "linux", reason="Linux-only two-phase syscall guard")
def test_gpu_discovery_native_socket_classes_and_sealed_exec():
    code = """
import ctypes,socket
from training.grpo_reload_isolation import LoopbackGuard
g=LoopbackGuard();g.install();g.install_syscall_guard(block_creation=False,gpu_discovery=True)
c=ctypes.CDLL(None,use_errno=True)
fd=c.socket(1,524293,0)
assert fd>=0
assert c.connect(fd,0,0)==-1 and ctypes.get_errno()==2
c.close(fd)
g.install_syscall_guard()
for number in (41,42,59,322,53):
 try:c.syscall(number,-1,0,0)
 except PermissionError:pass
 else:raise AssertionError(number)
assert len(g.attempts)==5
print('GPU_SEAL_REFUSALS_PASS')
"""
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("failing_stage", ["audit_hook", "import_syscall_guard"])
def test_guard_installation_failure_preserves_isolation_evidence(tmp_path, monkeypatch, failing_stage):
    # Codex finding (PR181): a failed guard install discarded the collected probes.
    path, sha = receipt(tmp_path)

    def refuse(self, **kwargs):
        raise PermissionError("Cannot install seccomp TSYNC guard")

    monkeypatch.setattr(
        isolation.LoopbackGuard, "install",
        refuse if failing_stage == "audit_hook" else (lambda self: None),
    )
    monkeypatch.setattr(isolation.LoopbackGuard, "install_syscall_guard", refuse)
    monkeypatch.setattr(isolation, "outbound_probes", failed_probes)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy", "all_proxy"):
        monkeypatch.delenv(name, raising=False)
    with pytest.raises(PermissionError) as raised:
        isolation.prepare_isolation(
            isolation.KAGGLE_PROFILE, receipt_path=path, receipt_sha256=sha, source_sha="a" * 40
        )
    evidence = raised.value.isolation_evidence
    assert evidence["outbound_probes"] == failed_probes()
    assert evidence["guard_installation_failure"] == {
        "stage": failing_stage, "error_type": "PermissionError",
    }
    assert not isolation.validate_isolation_evidence(evidence)


def gpu_discovery_evidence(*, empty_file=False, **socket_changes):
    stdout = "libcuda.so.1 (libc6,x86-64) => /usr/lib/x86_64-linux-gnu/libcuda.so.1\n"
    query = {
        "argv": ["/sbin/ldconfig", "-p"], "executable": "/sbin/ldconfig", "returncode": 0,
        "stdout": stdout, "stdout_sha256": hashlib.sha256(stdout.encode()).hexdigest(),
    }
    inventory = {"kernel.bin": {"sha256": hashlib.sha256(b"kernel").hexdigest(), "size": 6}}
    if empty_file:
        inventory["lock"] = {"sha256": hashlib.sha256(b"").hexdigest(), "size": 0}
    listener = {
        "fd": 7, "inode": "81234", "family": "AF_UNIX", "type": "SOCK_SEQPACKET",
        "state": "LISTENING_INERT", "pid_namespace_inode": "4026531836",
        "name": "@cuda-uvmfd-4026531836-4321@",
    }
    listener.update(socket_changes)
    return {
        "reload_pid": 4321,
        "context": {"runtime": {
            "triton_cache": "/kaggle/working/triton", "triton_inventory": inventory,
            "python_sha256": "b" * 64, "ldconfig_query": query,
        }},
        "runtime_discovery": {
            "phase": "SEALED_ARTIFACT_RELOAD", "gpu_count": 1, "python_sha256": "b" * 64,
            "runtime_inventory_before": inventory, "runtime_inventory_after": inventory,
            "architecture": ["64bit", "ELF"], "subprocesses_executed": [],
            "residual_sockets": [listener], "prepared_ldconfig_query": query,
        },
    }


def test_gpu_discovery_accepts_zero_byte_runtime_files():
    # Codex finding (PR182): discovery inventories empty lock files that acceptance refused.
    assert isolation.validate_gpu_discovery(gpu_discovery_evidence(empty_file=True))


@pytest.mark.parametrize("socket_changes", [
    {"name": "@cuda-uvmfd-arbitrary-4321@"},
    {"name": "@cuda-uvmfd-4026531999-4321@"},
    {"pid_namespace_inode": "not-an-inode"},
    {"pid_namespace_inode": None},
])
def test_gpu_discovery_requires_the_exact_namespace_bound_listener(socket_changes):
    # Codex finding (PR182): prefix/suffix checks admitted any middle component.
    assert isolation.validate_gpu_discovery(gpu_discovery_evidence())
    assert not isolation.validate_gpu_discovery(gpu_discovery_evidence(**socket_changes))


def test_gpu_discovery_rejects_listener_without_recorded_namespace():
    evidence = gpu_discovery_evidence()
    del evidence["runtime_discovery"]["residual_sockets"][0]["pid_namespace_inode"]
    assert not isolation.validate_gpu_discovery(evidence)
