"""Explicit reload profiles; no fallback from failed isolation to a weaker mode."""

from __future__ import annotations

import hashlib
import ipaddress
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NAMESPACE_PROFILE = "network-namespace-v1"
KAGGLE_PROFILE = "kaggle-verified-offline-v1"
PROBE_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 53), ("2606:4700:4700::1111", 443))
PROBE_TIMEOUT_SECONDS = 2.0


def require_network_isolation() -> dict[str, str]:
    interfaces = {path.name for path in Path("/sys/class/net").iterdir()}
    own = os.readlink("/proc/self/ns/net")
    host = os.readlink("/proc/1/ns/net")
    if own == host or interfaces - {"lo"}:
        raise ValueError("Controlled reload requires an isolated network namespace")
    return {"namespace": own, "host_namespace": host, "interfaces": ",".join(sorted(interfaces))}


def outbound_probes() -> list[dict[str, Any]]:
    """Direct IPs bypass DNS/proxy settings; each connection has a bounded timeout."""
    records = []
    for host, port in PROBE_TARGETS:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as probe:
            probe.settimeout(PROBE_TIMEOUT_SECONDS)
            try:
                probe.connect((host, port))
            except OSError as exc:
                records.append({
                    "target": host, "port": port, "connected": False,
                    "error_type": type(exc).__name__, "errno": exc.errno,
                    "timeout_seconds": PROBE_TIMEOUT_SECONDS,
                })
            else:
                records.append({
                    "target": host, "port": port, "connected": True,
                    "error_type": None, "errno": None,
                    "timeout_seconds": PROBE_TIMEOUT_SECONDS,
                })
    return records


class LoopbackGuard:
    """Permanent CPython socket/audit boundary for this dedicated reload process."""

    def __init__(self):
        self.attempts: list[dict[str, str]] = []

    def reject(self, operation: str) -> None:
        # Persist a latch even if a library catches the raised exception.
        self.attempts.append({"operation": operation, "outcome": "blocked"})
        raise PermissionError("Reload forbids external network and subprocess fallback")

    def check_address(self, operation: str, address: Any) -> None:
        if isinstance(address, str):  # AF_UNIX local IPC
            return
        if not isinstance(address, tuple) or len(address) < 2:
            self.reject(operation)
        try:
            local = ipaddress.ip_address(address[0]).is_loopback
        except (TypeError, ValueError):
            local = False
        if not local:
            self.reject(operation)

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event in {"socket.connect", "socket.sendto", "socket.sendmsg"}:
            self.check_address(event, args[-1])
        elif event == "socket.getaddrinfo":
            self.check_address(event, (args[0], args[1]))
        elif event in {"socket.gethostbyname", "socket.gethostbyaddr"}:
            self.check_address(event, (args[0], 0))
        elif event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec"}:
            self.reject(event)
        elif event == "socket.__new__":
            _, family, kind, _ = args
            if family not in {socket.AF_INET, socket.AF_INET6, getattr(socket, "AF_UNIX", -1)} or kind & 0xF == socket.SOCK_RAW:
                self.reject(event)

    def install(self) -> None:
        guard = self
        original = socket.socket

        class GuardedSocket(original):
            def connect_ex(self, address):
                guard.check_address("socket.connect_ex", address)
                return super().connect_ex(address)

        socket.socket = GuardedSocket
        import sys
        sys.addaudithook(self.audit)

    def require_clean(self) -> None:
        if self.attempts:
            raise PermissionError("Reload attempted a forbidden connection or fallback")


def prepare_isolation(
    profile: str, *, receipt_path: Path | None, receipt_sha256: str | None,
    source_sha: str,
) -> tuple[dict[str, Any], LoopbackGuard | None]:
    if profile == NAMESPACE_PROFILE:
        return {"reload_profile": profile, **require_network_isolation()}, None
    if profile != KAGGLE_PROFILE or receipt_path is None or receipt_sha256 is None:
        raise ValueError("Verified-offline reload requires a separately hash-bound receipt")
    from training.sft_provenance import has_redirecting_path_component

    if has_redirecting_path_component(receipt_path):
        raise ValueError("Offline receipt must not contain redirects")
    raw = receipt_path.read_bytes()
    if len(raw) > 16384 or hashlib.sha256(raw).hexdigest() != receipt_sha256:
        raise ValueError("Offline receipt hash mismatch")
    receipt = json.loads(raw)
    observed = datetime.fromisoformat(receipt["observed_at"])
    age = (datetime.now(UTC) - observed).total_seconds()
    if (
        receipt.get("reload_profile") != KAGGLE_PROFILE
        or receipt.get("source_sha") != source_sha
        or receipt.get("kaggle_internet_enabled") is not False
        or receipt.get("private_context") is not True
        or receipt.get("operator") != "Viraj Choudhary"
        or receipt.get("launcher_pid") != os.getppid()
        or os.getpid() == receipt.get("launcher_pid")
        or not 0 <= age <= 3600
    ):
        raise ValueError("Fresh private Internet-off context has not been bound to reload")
    if any(
        value for name, value in os.environ.items()
        if name.casefold() in {"http_proxy", "https_proxy", "all_proxy"}
    ):
        raise ValueError("Verified-offline reload forbids proxy configuration")
    os.environ.update(HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
    probes = outbound_probes()
    evidence = {
        "reload_profile": profile, "receipt_sha256": receipt_sha256,
        "context": receipt, "reload_pid": os.getpid(), "launcher_pid": os.getppid(),
        "outbound_probes": probes,
        "offline_flags": {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        "guard": "permanent_cpython_socket_audit_v1",
        "forbidden_attempts": [],
    }
    if any(record["connected"] for record in probes):
        error = PermissionError("External egress is available; reload NOT_VERIFIED")
        error.isolation_evidence = evidence
        raise error
    guard = LoopbackGuard()
    guard.install()
    return evidence, guard


def validate_isolation_evidence(value: Any) -> bool:
    if not isinstance(value, dict):
        return False
    profile = value.get("reload_profile", NAMESPACE_PROFILE)
    if profile == NAMESPACE_PROFILE:
        return bool(
            value.get("namespace") and value.get("host_namespace")
            and value["namespace"] != value["host_namespace"]
            and value.get("interfaces") in {"", "lo"}
        )
    if profile != KAGGLE_PROFILE:
        return False
    context = value.get("context", {})
    probes = value.get("outbound_probes")
    return (
        context.get("kaggle_internet_enabled") is False
        and context.get("private_context") is True
        and type(value.get("reload_pid")) is int and value["reload_pid"] > 0
        and type(value.get("launcher_pid")) is int and value["launcher_pid"] > 0
        and value.get("reload_pid") != value.get("launcher_pid")
        and context.get("launcher_pid") == value.get("launcher_pid")
        and isinstance(value.get("receipt_sha256"), str) and len(value["receipt_sha256"]) == 64
        and value.get("offline_flags") == {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        and value.get("guard") == "permanent_cpython_socket_audit_v1"
        and value.get("forbidden_attempts") == []
        and isinstance(probes, list) and len(probes) == len(PROBE_TARGETS)
        and [(r.get("target"), r.get("port")) for r in probes] == list(PROBE_TARGETS)
        and all(r.get("connected") is False and r.get("error_type") for r in probes)
    )
