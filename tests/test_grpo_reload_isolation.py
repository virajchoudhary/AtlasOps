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
        {"target": host, "port": port, "connected": False, "error_type": "TimeoutError"}
        for host, port in isolation.PROBE_TARGETS
    ]


def test_offline_profile_requires_receipt_and_actual_egress_failure(tmp_path, monkeypatch):
    path, sha = receipt(tmp_path)
    monkeypatch.setattr(isolation.LoopbackGuard, "install", lambda self: None)
    monkeypatch.setattr(isolation.LoopbackGuard, "install_syscall_guard", lambda self: None)
    monkeypatch.setattr(isolation, "outbound_probes", failed_probes)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "http_proxy", "https_proxy"):
        monkeypatch.delenv(name, raising=False)
    evidence, guard = isolation.prepare_isolation(
        isolation.KAGGLE_PROFILE, receipt_path=path, receipt_sha256=sha, source_sha="a" * 40
    )
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
    monkeypatch.setattr(isolation.LoopbackGuard, "install_syscall_guard", lambda self: None)
    monkeypatch.setattr(isolation, "outbound_probes", failed_probes)
    for name in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY"):
        monkeypatch.delenv(name, raising=False)
    evidence, _ = isolation.prepare_isolation(
        isolation.KAGGLE_PROFILE, receipt_path=path, receipt_sha256=sha, source_sha="a" * 40
    )
    evidence["forbidden_attempts"] = [{"operation": "socket.connect", "outcome": "blocked"}]
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
