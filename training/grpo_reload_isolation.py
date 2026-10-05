"""Explicit reload profiles; no fallback from failed isolation to a weaker mode."""

from __future__ import annotations

import hashlib
import errno
import ipaddress
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

NAMESPACE_PROFILE = "network-namespace-v1"
KAGGLE_PROFILE = "kaggle-verified-offline-v1"
KAGGLE_GPU_PROFILE = "kaggle-verified-offline-gpu-v2"
PROBE_TARGETS = (("1.1.1.1", 443), ("8.8.8.8", 53), ("2606:4700:4700::1111", 443))
PROBE_TIMEOUT_SECONDS = 2.0
RECEIPT_KEYS = frozenset({
    "reload_profile", "source_sha", "observed_at", "kaggle_internet_enabled",
    "private_context", "operator", "launcher_pid",
})


def probe_proves_blocked(record: dict[str, Any]) -> bool:
    return record.get("connected") is False and (
        record.get("error_type") in {"TimeoutError", "PermissionError"}
        or record.get("errno") in {errno.ENETUNREACH, errno.EHOSTUNREACH, errno.EACCES, errno.EPERM, errno.ETIMEDOUT}
    )


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
        self.gpu_discovery = False
        self.sealed = False

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
            if self.gpu_discovery:
                self.reject(event)
            self.check_address(event, args[-1])
        elif self.gpu_discovery and event == "socket.bind":
            namespace = os.readlink("/proc/self/ns/pid")
            inode = namespace.removeprefix("pid:[").removesuffix("]")
            expected = f"\0cuda-uvmfd-{inode}-{os.getpid()}\0".encode()
            if self.sealed or args[-1] not in {expected, expected.decode()}:
                self.reject(event)
        elif event == "socket.getaddrinfo":
            self.check_address(event, (args[0], args[1]))
        elif event in {"socket.gethostbyname", "socket.gethostbyaddr"}:
            self.check_address(event, (args[0], 0))
        elif event in {"subprocess.Popen", "os.system", "os.posix_spawn", "os.exec", "os.fork", "os.forkpty"}:
            self.reject(event)
        elif event == "socket.__new__":
            _, family, kind, protocol = args
            if self.gpu_discovery:
                if family != socket.AF_UNIX or kind & 0xf != socket.SOCK_SEQPACKET or protocol != 0:
                    self.reject(event)
                return
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

    def install_syscall_guard(self, *, block_creation: bool = True, gpu_discovery: bool = False) -> None:
        """Linux x86_64 seccomp TSYNC blocks even native connect/send syscalls."""
        import ctypes
        import platform
        import signal

        if platform.system() != "Linux" or platform.machine() != "x86_64":
            raise ValueError("Verified-offline profile requires Linux x86_64 seccomp")
        for descriptor in Path("/proc/self/fd").iterdir():
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue
            if target.startswith("socket:"):
                if not (self.gpu_discovery and block_creation):
                    raise PermissionError("Reload process must inherit no socket descriptors")

        class Filter(ctypes.Structure):
            _fields_ = [("code", ctypes.c_ushort), ("jt", ctypes.c_ubyte),
                        ("jf", ctypes.c_ubyte), ("k", ctypes.c_uint)]

        class Program(ctypes.Structure):
            _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.POINTER(Filter))]

        # Check architecture, then trap every connect/sendto/sendmsg syscall.
        # Blocking all socket sends is deliberately stricter than loopback-only.
        instructions = [(0x20, 0, 0, 4), (0x15, 1, 0, 0xC000003E),
                        (0x06, 0, 0, 0x80000000), (0x20, 0, 0, 0),
                        (0x45, 0, 1, 0x40000000), (0x06, 0, 0, 0x80000000)]
        numbers = (42, 44, 46, 307, 425, 426)
        if gpu_discovery or self.gpu_discovery:
            # No descriptor import, accepted connection, native exec or network fallback.
            numbers = (44, 45, 46, 47, 43, 288, 299, 307, 425, 426, 427,
                       53, 57, 58, 59, 322, 101, 438, 272, 308)
            # Thread creation is required by CUDA; process clones are not.
            instructions += [
                (0x15, 0, 3, 56), (0x20, 0, 0, 16),
                (0x45, 1, 0, 0x10000), (0x06, 0, 0, 0x00030000),
                (0x20, 0, 0, 0),
                (0x15, 0, 1, 435), (0x06, 0, 0, 0x00050000 | errno.ENOSYS),
            ]
            if block_creation:
                numbers += (42, 49, 50)
            else:
                # CUDA's optional MPS query is refused before the syscall executes.
                # Only the exact unconnected Unix class can exist in discovery.
                instructions += [(0x15, 0, 1, 42), (0x06, 0, 0, 0x00050000 | errno.ENOENT)]
                # The traced local listener uses backlog 128 and SO_PASSCRED only.
                for syscall_number, offset, required in (
                    (50, 24, 128), (54, 24, 1), (54, 32, 16), (54, 48, 4),
                ):
                    instructions += [
                        (0x15, 0, 3, syscall_number), (0x20, 0, 0, offset),
                        (0x15, 1, 0, required), (0x06, 0, 0, 0x00030000),
                        (0x20, 0, 0, 0),
                    ]
        if block_creation:
            numbers += (41, 53)
        for number in numbers:
            instructions += [(0x15, 0, 1, number), (0x06, 0, 0, 0x00030000)]
        if gpu_discovery and not block_creation:
            instructions += [
                (0x15, 0, 7, 41), (0x20, 0, 0, 16),
                (0x15, 0, 4, socket.AF_UNIX), (0x20, 0, 0, 24),
                (0x15, 0, 2, socket.SOCK_SEQPACKET | socket.SOCK_CLOEXEC),
                (0x20, 0, 0, 32), (0x15, 1, 0, 0),
                (0x06, 0, 0, 0x00030000),
            ]
        elif not block_creation:
            # socket syscall: allow only IP/Unix families, never raw sockets.
            instructions += [
                (0x15, 0, 9, 41), (0x20, 0, 0, 16),
                (0x15, 3, 0, 1), (0x15, 2, 0, 2), (0x15, 1, 0, 10),
                (0x06, 0, 0, 0x00030000), (0x20, 0, 0, 24),
                (0x54, 0, 0, 0xF), (0x15, 0, 1, 3),
                (0x06, 0, 0, 0x00030000),
            ]
        instructions += [(0x06, 0, 0, 0x7FFF0000)]
        array = (Filter * len(instructions))(*(Filter(*row) for row in instructions))
        program = Program(len(instructions), array)
        libc = ctypes.CDLL(None, use_errno=True)

        def violation(_number, _frame):
            self.reject("native_socket_syscall")

        signal.signal(signal.SIGSYS, violation)
        if libc.prctl(38, 1, 0, 0, 0) != 0:
            raise PermissionError("Cannot establish no_new_privs")
        if libc.syscall(317, 1, 1, ctypes.byref(program)) != 0:
            raise PermissionError("Cannot install seccomp TSYNC guard")
        if gpu_discovery:
            self.gpu_discovery = True

    def seal_gpu_runtime(self) -> list[dict[str, Any]]:
        """Tighten first, then prove the only residual socket is inert CUDA IPC."""
        self.require_clean()
        self.install_syscall_guard()
        self.sealed = True
        namespace = os.readlink("/proc/self/ns/pid")
        inode = namespace.removeprefix("pid:[").removesuffix("]")
        expected = f"@cuda-uvmfd-{inode}-{os.getpid()}@"
        entries = {}
        for line in Path("/proc/net/unix").read_text().splitlines()[1:]:
            columns = line.split()
            if len(columns) >= 7:
                entries[columns[6]] = columns
        residual = []
        for descriptor in Path("/proc/self/fd").iterdir():
            try:
                target = os.readlink(descriptor)
            except FileNotFoundError:
                continue
            if not target.startswith("socket:["):
                continue
            socket_inode = target[8:-1]
            columns = entries.get(socket_inode, [])
            if (
                len(columns) != 8 or columns[4:6] != ["0005", "01"]
                or columns[3] != "00010000" or columns[7] != expected
            ):
                self.reject("unexpected_residual_socket")
            residual.append({"fd": int(descriptor.name), "inode": socket_inode,
                             "family": "AF_UNIX", "type": "SOCK_SEQPACKET",
                             "state": "LISTENING_INERT", "name": expected})
        if len(residual) != 1:
            self.reject("missing_or_duplicate_cuda_listener")
        return residual


def initialize_gpu_runtime(runtime: dict[str, Any], guard: LoopbackGuard) -> dict[str, Any]:
    """Local package discovery only; this function never opens model artifacts."""
    import platform
    import sys
    from training.sft_provenance import has_redirecting_path_component

    root = Path(runtime["triton_cache"])
    if has_redirecting_path_component(root) or not root.is_absolute():
        raise ValueError("Runtime cache must be local and redirect-free")
    inventory = {}
    for path in sorted(root.rglob("*")):
        if has_redirecting_path_component(path):
            raise ValueError("Redirected runtime cache")
        if path.is_file():
            inventory[path.relative_to(root).as_posix()] = {
                "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "size": path.stat().st_size,
            }
    if not inventory or inventory != runtime["triton_inventory"]:
        raise ValueError("Missing or changed prepared Triton runtime cache")
    executable = Path(sys.executable).resolve()
    with executable.open("rb") as stream:
        header = stream.read(20)
    if header[:6] != b"\x7fELF\x02\x01" or header[18:20] != b"\x3e\x00":
        raise ValueError("Runtime requires the verified local x86_64 ELF interpreter")
    if hashlib.sha256(executable.read_bytes()).hexdigest() != runtime["python_sha256"]:
        raise ValueError("Local interpreter hash mismatch")
    query = runtime["ldconfig_query"]
    query_raw = query["stdout"].encode("utf-8")
    if (
        query["executable"] != "/sbin/ldconfig" or query["argv"] != ["/sbin/ldconfig", "-p"]
        or query["returncode"] != 0 or len(query_raw) > 1048576
        or hashlib.sha256(query_raw).hexdigest() != query["stdout_sha256"]
        or hashlib.sha256(Path("/sbin/ldconfig").read_bytes()).hexdigest() != query["executable_sha256"]
    ):
        raise ValueError("Prepared exact local ldconfig query evidence mismatch")
    # Triton calls this uncached platform query when deriving its local cache key.
    original_architecture = platform.architecture

    def local_architecture(executable=sys.executable, bits="", linkage=""):
        if Path(executable).resolve() != Path(sys.executable).resolve() or bits or linkage:
            guard.reject("unexpected_platform_query")
        return ("64bit", "ELF")

    platform.architecture = local_architecture
    os.environ["TRITON_CACHE_DIR"] = str(root)
    try:
        import torch
        torch.cuda.init()
        import bitsandbytes
        import triton
        from transformers import AutoModelForCausalLM, AutoTokenizer  # noqa: F401 - initialize reload classes
        from peft import PeftModel  # noqa: F401 - initialize reload class
        if not torch.cuda.is_available() or not bitsandbytes.cextension.lib.compiled_with_cuda:
            raise ValueError("Local GPU runtime unavailable")
        guard.require_clean()
        residual = guard.seal_gpu_runtime()
    finally:
        platform.architecture = original_architecture
    after = {
        p.relative_to(root).as_posix(): {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(), "size": p.stat().st_size,
        } for p in sorted(root.rglob("*")) if p.is_file()
    }
    if after != inventory:
        raise ValueError("Runtime cache changed during discovery")
    return {"phase": "SEALED_ARTIFACT_RELOAD", "gpu_count": torch.cuda.device_count(),
            "runtime_inventory_before": inventory, "runtime_inventory_after": after,
            "python_sha256": runtime["python_sha256"], "architecture": ["64bit", "ELF"],
            "subprocesses_executed": [], "residual_sockets": residual,
            "prepared_ldconfig_query": query}


def prepare_isolation(
    profile: str, *, receipt_path: Path | None, receipt_sha256: str | None,
    source_sha: str,
) -> tuple[dict[str, Any], LoopbackGuard | None]:
    if profile == NAMESPACE_PROFILE:
        return {"reload_profile": profile, **require_network_isolation()}, None
    if profile not in {KAGGLE_PROFILE, KAGGLE_GPU_PROFILE} or receipt_path is None or receipt_sha256 is None:
        raise ValueError("Verified-offline reload requires a separately hash-bound receipt")
    from training.sft_provenance import has_redirecting_path_component

    if has_redirecting_path_component(receipt_path):
        raise ValueError("Offline receipt must not contain redirects")
    raw = receipt_path.read_bytes()
    if len(raw) > (2 * 1048576 if profile == KAGGLE_GPU_PROFILE else 16384) or hashlib.sha256(raw).hexdigest() != receipt_sha256:
        raise ValueError("Offline receipt hash mismatch")
    receipt = json.loads(raw)
    expected_keys = RECEIPT_KEYS | ({"runtime"} if profile == KAGGLE_GPU_PROFILE else set())
    if not isinstance(receipt, dict) or set(receipt) != expected_keys:
        raise ValueError("Offline receipt must contain only sanitized context fields")
    observed = datetime.fromisoformat(receipt["observed_at"])
    if observed.tzinfo is None:
        raise ValueError("Offline receipt observation requires a timezone")
    age = (datetime.now(UTC) - observed).total_seconds()
    if (
        receipt.get("reload_profile") != profile
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
    if profile == KAGGLE_GPU_PROFILE:
        # Close urllib3's import-time ::1 capability socket before the guards.
        import urllib3.util.connection  # noqa: F401 - close import-time capability socket
    probes = outbound_probes()
    evidence = {
        "reload_profile": profile, "receipt_sha256": receipt_sha256,
        "context": receipt, "reload_pid": os.getpid(), "launcher_pid": os.getppid(),
        "receipt_raw": raw.decode("utf-8"),
        "outbound_probes": probes,
        "offline_flags": {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
        "guard": "cpython_audit_and_seccomp_tsync_gpu_v2" if profile == KAGGLE_GPU_PROFILE else "cpython_audit_and_seccomp_tsync_v1",
        "inherited_socket_fds": [],
        "syscall_guard_installed": False,
        "forbidden_attempts": [],
    }
    if not all(probe_proves_blocked(record) for record in probes):
        error = PermissionError("External egress is available; reload NOT_VERIFIED")
        error.isolation_evidence = evidence
        raise error
    guard = LoopbackGuard()
    guard.install()
    guard.install_syscall_guard(block_creation=False, gpu_discovery=profile == KAGGLE_GPU_PROFILE)
    evidence["import_syscall_guard_installed"] = True
    return evidence, guard


def validate_isolation_evidence(value: Any, *, source_sha: str | None = None) -> bool:
    if not isinstance(value, dict):
        return False
    profile = value.get("reload_profile", NAMESPACE_PROFILE)
    if profile == NAMESPACE_PROFILE:
        return bool(
            value.get("namespace") and value.get("host_namespace")
            and value["namespace"] != value["host_namespace"]
            and value.get("interfaces") in {"", "lo"}
        )
    if profile not in {KAGGLE_PROFILE, KAGGLE_GPU_PROFILE}:
        return False
    context = value.get("context", {})
    probes = value.get("outbound_probes")
    try:
        raw = value["receipt_raw"].encode("utf-8")
        receipt_matches = (
            json.loads(raw) == context
            and set(context) == RECEIPT_KEYS | ({"runtime"} if profile == KAGGLE_GPU_PROFILE else set())
            and hashlib.sha256(raw).hexdigest() == value["receipt_sha256"]
            and context["operator"] == "Viraj Choudhary"
            and context["reload_profile"] == profile
            and (source_sha is None or context["source_sha"] == source_sha)
            and datetime.fromisoformat(context["observed_at"]).tzinfo is not None
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
    if profile == KAGGLE_GPU_PROFILE and not validate_gpu_discovery(value):
        return False
    return (
        receipt_matches
        and
        context.get("kaggle_internet_enabled") is False
        and context.get("private_context") is True
        and type(value.get("reload_pid")) is int and value["reload_pid"] > 0
        and type(value.get("launcher_pid")) is int and value["launcher_pid"] > 0
        and value.get("reload_pid") != value.get("launcher_pid")
        and context.get("launcher_pid") == value.get("launcher_pid")
        and isinstance(value.get("receipt_sha256"), str) and len(value["receipt_sha256"]) == 64
        and value.get("offline_flags") == {"HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"}
        and value.get("guard") == (
            "cpython_audit_and_seccomp_tsync_gpu_v2" if profile == KAGGLE_GPU_PROFILE
            else "cpython_audit_and_seccomp_tsync_v1")
        and value.get("inherited_socket_fds") == []
        and value.get("syscall_guard_installed") is True
        and value.get("import_syscall_guard_installed") is True
        and value.get("forbidden_attempts") == []
        and isinstance(probes, list) and len(probes) == len(PROBE_TARGETS)
        and [(r.get("target"), r.get("port")) for r in probes] == list(PROBE_TARGETS)
        and all(probe_proves_blocked(r) for r in probes)
    )


def validate_gpu_discovery(value: dict[str, Any]) -> bool:
    """Reject missing discovery proof rather than treating a profile label as proof."""
    try:
        runtime = value["context"]["runtime"]
        discovery = value["runtime_discovery"]
        inventory = runtime["triton_inventory"]
        sockets = discovery["residual_sockets"]
        return (
            set(runtime) == {"triton_cache", "triton_inventory", "python_sha256", "ldconfig_query"}
            and isinstance(runtime["triton_cache"], str) and runtime["triton_cache"].startswith("/")
            and isinstance(inventory, dict) and bool(inventory)
            and all(
                isinstance(name, str) and not name.startswith("/") and ".." not in name.split("/")
                and set(record) == {"sha256", "size"}
                and isinstance(record["sha256"], str)
                and len(record["sha256"]) == 64
                and all(c in "0123456789abcdef" for c in record["sha256"])
                and type(record["size"]) is int and record["size"] > 0
                for name, record in inventory.items()
            )
            and isinstance(runtime["python_sha256"], str) and len(runtime["python_sha256"]) == 64
            and discovery["python_sha256"] == runtime["python_sha256"]
            and discovery["runtime_inventory_before"] == inventory
            and discovery["runtime_inventory_after"] == inventory
            and discovery["architecture"] == ["64bit", "ELF"]
            and discovery["phase"] == "SEALED_ARTIFACT_RELOAD"
            and type(discovery["gpu_count"]) is int and discovery["gpu_count"] > 0
            and discovery["subprocesses_executed"] == []
            and discovery["prepared_ldconfig_query"] == runtime["ldconfig_query"]
            and runtime["ldconfig_query"]["argv"] == ["/sbin/ldconfig", "-p"]
            and runtime["ldconfig_query"]["executable"] == "/sbin/ldconfig"
            and runtime["ldconfig_query"]["returncode"] == 0
            and hashlib.sha256(runtime["ldconfig_query"]["stdout"].encode()).hexdigest() == runtime["ldconfig_query"]["stdout_sha256"]
            and isinstance(sockets, list) and len(sockets) == 1
            and sockets[0]["family"] == "AF_UNIX"
            and sockets[0]["type"] == "SOCK_SEQPACKET"
            and sockets[0]["state"] == "LISTENING_INERT"
            and type(sockets[0]["fd"]) is int and sockets[0]["fd"] >= 0
            and sockets[0]["inode"].isdigit()
            and sockets[0]["name"].startswith("@cuda-uvmfd-")
            and sockets[0]["name"].endswith(f"-{value['reload_pid']}@")
        )
    except (KeyError, TypeError, ValueError, AttributeError):
        return False
