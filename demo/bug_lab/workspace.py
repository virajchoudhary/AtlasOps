"""The editable working copy of the Atlas Store, kept apart from the pristine release."""

from __future__ import annotations

import copy
import difflib
import hashlib
import json
import math
import os
import shutil
import stat
import time
import uuid
import weakref
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SITE_SOURCE = Path(__file__).resolve().parent / "site"
EDITABLE_FILES = ("index.html", "styles.css", "app.js", "products.json", "config.json")
MONITOR_TAG = '<script src="/lab/monitor.js"></script>'
_MARKER_NAME = ".bug-lab-workspace.json"
_LOCK_NAME = ".bug-lab-workspace.lock"
_LOG_NAME = "incidents.jsonl"
_PENDING_NAME = "pending.json"
_WORKSPACE_SCHEMA_VERSION = 1
_EVIDENCE_SCHEMA_VERSION = 1
_EVIDENCE_CLASSIFICATION = "LOCAL_DEMONSTRATION_NOT_SCIENTIFIC"
_PERSISTENCE_ERROR = "Bug Lab workspace persistence failed"


def has_redirecting_path_component(path: Path) -> bool:
    """Identify symlink, Windows reparse, or hard-link path redirects."""
    absolute_path = Path(os.path.abspath(path))
    reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
    for component in (absolute_path, *absolute_path.parents):
        try:
            details = component.lstat()
        except FileNotFoundError:
            continue
        except OSError:
            return True
        if stat.S_ISLNK(details.st_mode):
            return True
        if getattr(details, "st_file_attributes", 0) & reparse_attribute:
            return True
        if (
            component == absolute_path
            and not stat.S_ISDIR(details.st_mode)
            and getattr(details, "st_nlink", 1) > 1
        ):
            return True
    return False


@dataclass(frozen=True)
class Recovery:
    workspace_id: str
    sequence: int
    records: tuple[dict[str, Any], ...]


class WorkspacePersistenceError(RuntimeError):
    """A credential-safe failure from an owned workspace persistence operation."""


class _WorkspaceInputError(ValueError):
    pass


class _WorkspaceIntegrityError(ValueError):
    pass


class _WorkspaceLockedError(ValueError):
    pass


def _close_descriptor(fd: int) -> None:
    try:
        os.close(fd)
    except OSError:
        pass


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise _WorkspaceIntegrityError("Bug Lab workspace JSON has duplicate keys")
        value[key] = item
    return value


def _reject_json_constant(value: str):
    raise _WorkspaceIntegrityError("Bug Lab workspace JSON is malformed")


def _load_json_object(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(
            data.decode("utf-8"),
            object_pairs_hook=_unique_object,
            parse_constant=_reject_json_constant,
        )
    except _WorkspaceIntegrityError:
        raise
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise _WorkspaceIntegrityError("Bug Lab workspace JSON is malformed") from None
    if not isinstance(value, dict):
        raise _WorkspaceIntegrityError("Bug Lab workspace JSON must be an object")
    return value


def _serialize_json(value: dict[str, Any]) -> bytes:
    try:
        return json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    except (TypeError, ValueError):
        raise _WorkspaceInputError("Incident record must be JSON serializable") from None


def _write_all(fd: int, data: bytes) -> None:
    remaining = memoryview(data)
    while remaining:
        written = os.write(fd, remaining)
        if written <= 0:
            raise OSError("incomplete workspace write")
        remaining = remaining[written:]


def _read_all(fd: int) -> bytes:
    chunks = []
    while True:
        chunk = os.read(fd, 1024 * 1024)
        if not chunk:
            return b"".join(chunks)
        chunks.append(chunk)


def _sync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


class Workspace:
    """Bug injection and agent patches only ever write files inside ``root / "site"``."""

    def __init__(
        self,
        root: Path,
        source: Path = SITE_SOURCE,
        *,
        resume: bool = False,
    ) -> None:
        root_path = Path(os.path.abspath(root))
        if has_redirecting_path_component(root_path):
            raise ValueError("Bug Lab workdir must not traverse a symbolic link or junction")

        if not root_path.exists():
            if resume:
                raise ValueError("Bug Lab resume requires an existing owned workspace")
            root_path.mkdir(parents=True, exist_ok=True)
        if not root_path.is_dir():
            raise ValueError("Bug Lab workdir must be a directory")
        if has_redirecting_path_component(root_path):
            raise ValueError("Bug Lab workdir must not traverse a symbolic link or junction")

        self.root = root_path.resolve(strict=True)
        self.source = source.resolve()
        self.site = self.root / "site"
        self.log_path = self.root / _LOG_NAME
        self._marker_path = self.root / _MARKER_NAME
        self._lock_path = self.root / _LOCK_NAME
        self._pending_path = self.root / _PENDING_NAME
        self._workspace_id: str | None = None
        self._sequence = 0
        self._records: list[dict[str, Any]] = []
        self._pending_record: dict[str, Any] | None = None
        self._healthy = True
        self._closed = False
        self._lock_fd: int | None = None
        self._lock_finalizer: weakref.finalize | None = None

        entries = self._entry_names()
        if resume:
            if _MARKER_NAME not in entries:
                raise ValueError("Bug Lab resume requires a valid workspace marker")
        elif entries:
            raise ValueError("Bug Lab workdir must be new or empty")

        try:
            self._acquire_lock(create=not resume)
            if resume:
                self._open_existing()
            else:
                self._create_fresh()
        except BaseException as exc:
            self.close()
            if isinstance(
                exc,
                (_WorkspaceIntegrityError, _WorkspaceLockedError, WorkspacePersistenceError),
            ):
                raise
            if isinstance(exc, (KeyboardInterrupt, SystemExit)):
                raise
            self._healthy = False
            raise WorkspacePersistenceError(_PERSISTENCE_ERROR) from None

    @property
    def workspace_id(self) -> str:
        if self._workspace_id is None:
            raise WorkspacePersistenceError(_PERSISTENCE_ERROR)
        return self._workspace_id

    @property
    def healthy(self) -> bool:
        return self._healthy and not self._closed

    def _entry_names(self) -> set[str]:
        try:
            with os.scandir(self.root) as entries:
                return {entry.name for entry in entries}
        except FileNotFoundError:
            return set()

    @staticmethod
    def _same_file(left, right) -> bool:
        return left.st_dev == right.st_dev and left.st_ino == right.st_ino

    def _file_stat(self, path: Path, *, required: bool = False):
        if has_redirecting_path_component(path):
            raise _WorkspaceIntegrityError("Bug Lab workspace contains a redirected file")
        try:
            details = path.lstat()
        except FileNotFoundError:
            if required:
                raise _WorkspaceIntegrityError("Bug Lab workspace file is missing")
            return None
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISREG(details.st_mode)
            or getattr(details, "st_file_attributes", 0) & reparse_attribute
            or getattr(details, "st_nlink", 1) > 1
        ):
            raise _WorkspaceIntegrityError("Bug Lab workspace contains a redirected file")
        return details

    def _directory_stat(self, path: Path, *, required: bool = True):
        if has_redirecting_path_component(path):
            raise _WorkspaceIntegrityError("Bug Lab workspace contains a redirected directory")
        try:
            details = path.lstat()
        except FileNotFoundError:
            if required:
                raise _WorkspaceIntegrityError("Bug Lab workspace directory is missing")
            return None
        reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
        if (
            not stat.S_ISDIR(details.st_mode)
            or getattr(details, "st_file_attributes", 0) & reparse_attribute
        ):
            raise _WorkspaceIntegrityError("Bug Lab workspace contains a redirected directory")
        return details

    def _open_regular_file(self, path: Path, flags: int, *, mode: int = 0o600) -> int:
        self._file_stat(path, required=not bool(flags & os.O_CREAT))
        no_follow = getattr(os, "O_NOFOLLOW", 0)
        binary = getattr(os, "O_BINARY", 0)
        fd = os.open(path, flags | no_follow | binary, mode)
        try:
            opened = os.fstat(fd)
            reparse_attribute = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)
            if (
                not stat.S_ISREG(opened.st_mode)
                or getattr(opened, "st_file_attributes", 0) & reparse_attribute
                or getattr(opened, "st_nlink", 1) > 1
            ):
                raise _WorkspaceIntegrityError("Bug Lab workspace contains a redirected file")
            current = self._file_stat(path, required=True)
            if not self._same_file(opened, current):
                raise _WorkspaceIntegrityError("Bug Lab workspace path changed during access")
            return fd
        except BaseException:
            os.close(fd)
            raise

    def _read_owned_file(self, path: Path, *, required: bool = False) -> bytes | None:
        if self._file_stat(path, required=required) is None:
            return None
        fd = self._open_regular_file(path, os.O_RDONLY)
        try:
            return _read_all(fd)
        finally:
            os.close(fd)

    def _atomic_write_raw(self, path: Path, data: bytes) -> None:
        temporary = path.with_name(f".{path.name}.tmp")
        if self._file_stat(temporary) is not None:
            raise _WorkspaceIntegrityError("Bug Lab workspace has an unfinished temporary file")
        self._file_stat(path)
        fd = self._open_regular_file(
            temporary,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
        )
        try:
            _write_all(fd, data)
            os.fsync(fd)
        finally:
            os.close(fd)
        self._file_stat(path)
        os.replace(temporary, path)
        self._file_stat(path, required=True)
        _sync_directory(path.parent)

    def _validate_layout(self, *, require_site: bool = True, scan_site: bool = False) -> None:
        self._directory_stat(self.root)
        entries = self._entry_names()
        allowed = {
            "site",
            _MARKER_NAME,
            _LOCK_NAME,
            _LOG_NAME,
            _PENDING_NAME,
        }
        if entries - allowed:
            raise _WorkspaceIntegrityError("Bug Lab workspace has an unknown layout entry")
        if _LOCK_NAME not in entries:
            raise _WorkspaceIntegrityError("Bug Lab workspace lock is missing")
        self._file_stat(self._lock_path, required=True)
        if _MARKER_NAME in entries:
            self._file_stat(self._marker_path, required=True)
        elif require_site:
            raise _WorkspaceIntegrityError("Bug Lab workspace marker is missing")
        if _LOG_NAME in entries:
            self._file_stat(self.log_path, required=True)
        if _PENDING_NAME in entries:
            self._file_stat(self._pending_path, required=True)
        if "site" in entries:
            self._directory_stat(self.site)
            if scan_site:
                self._validate_site_tree()
        elif require_site:
            raise _WorkspaceIntegrityError("Bug Lab workspace store is missing")

    def _validate_site_tree(self) -> None:
        self._directory_stat(self.site)
        allowed_paths = {path.relative_to(self.source) for path in self.source.rglob("*")}

        def fail_on_walk_error(_error):
            raise _WorkspaceIntegrityError("Bug Lab store could not be inspected")

        for directory, subdirectories, filenames in os.walk(
            self.site,
            topdown=True,
            onerror=fail_on_walk_error,
            followlinks=False,
        ):
            parent = Path(directory)
            self._directory_stat(parent)
            for name in subdirectories:
                if (parent / name).relative_to(self.site) not in allowed_paths:
                    raise _WorkspaceIntegrityError("Bug Lab store has an unknown layout entry")
                self._directory_stat(parent / name)
            for name in filenames:
                if (parent / name).relative_to(self.site) not in allowed_paths:
                    raise _WorkspaceIntegrityError("Bug Lab store has an unknown layout entry")
                self._file_stat(parent / name, required=True)

    def _read_marker(self) -> str:
        raw = self._read_owned_file(self._marker_path, required=True)
        marker = _load_json_object(raw)
        if (
            set(marker) != {"schema_version", "workspace_id"}
            or type(marker.get("schema_version")) is not int
            or marker["schema_version"] != _WORKSPACE_SCHEMA_VERSION
            or not isinstance(marker.get("workspace_id"), str)
        ):
            raise _WorkspaceIntegrityError("Bug Lab workspace marker is invalid")
        try:
            identity = str(uuid.UUID(marker["workspace_id"]))
        except (ValueError, AttributeError):
            raise _WorkspaceIntegrityError("Bug Lab workspace marker is invalid") from None
        if identity != marker["workspace_id"]:
            raise _WorkspaceIntegrityError("Bug Lab workspace marker is invalid")
        return identity

    def _acquire_lock(self, *, create: bool) -> None:
        fd = self._open_regular_file(
            self._lock_path,
            os.O_RDWR | (os.O_CREAT if create else 0),
        )
        try:
            if os.name == "nt":
                import msvcrt

                if os.fstat(fd).st_size == 0:
                    os.lseek(fd, 0, os.SEEK_SET)
                    _write_all(fd, b"\0")
                    os.fsync(fd)
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            raise _WorkspaceLockedError("Bug Lab workspace is already in use") from None
        self._lock_fd = fd
        self._lock_finalizer = weakref.finalize(self, _close_descriptor, fd)

    def _create_fresh(self) -> None:
        if self._entry_names() != {_LOCK_NAME}:
            raise ValueError("Bug Lab workdir must be new or empty")
        if not self.source.is_dir():
            raise _WorkspaceIntegrityError("Bug Lab release store is missing")
        self._workspace_id = str(uuid.uuid4())
        shutil.copytree(self.source, self.site)
        self._validate_site_tree()
        marker = {
            "schema_version": _WORKSPACE_SCHEMA_VERSION,
            "workspace_id": self._workspace_id,
        }
        self._atomic_write_raw(self._marker_path, _serialize_json(marker) + b"\n")

    def _validate_record(self, record: dict[str, Any], *, terminal: bool = False) -> None:
        sequence = record.get("sequence")
        if (
            type(sequence) is not int
            or sequence < 1
            or record.get("workspace_id") != self.workspace_id
            or record.get("id") != f"INC-{sequence:04d}"
            or type(record.get("evidence_schema_version")) is not int
            or record["evidence_schema_version"] != _EVIDENCE_SCHEMA_VERSION
            or record.get("evidence_classification") != _EVIDENCE_CLASSIFICATION
        ):
            raise _WorkspaceIntegrityError("Bug Lab incident record identity is invalid")
        required_fields = {
            "status", "stage", "active_agent", "active_backend", "opened_at", "closed_at",
            "severity", "title", "injected", "alerts", "detect_s", "resolve_s", "diagnosis",
            "patch", "approval", "attempts", "events", "resolved_by", "outcome", "summary",
            "diagnosis_matched", "version_at_open", "backend", "verification",
        }
        if not required_fields.issubset(record):
            raise _WorkspaceIntegrityError("Bug Lab incident record shape is invalid")
        if not isinstance(record["status"], str) or not isinstance(record["stage"], str):
            raise _WorkspaceIntegrityError("Bug Lab incident record shape is invalid")
        if not all(
            value is None or isinstance(value, str)
            for value in (
                record["active_agent"], record["active_backend"], record["severity"],
                record["title"], record["resolved_by"], record["outcome"], record["summary"],
            )
        ):
            raise _WorkspaceIntegrityError("Bug Lab incident record shape is invalid")
        if not self._is_number(record["opened_at"]) or not self._is_optional_number(
            record["closed_at"]
        ):
            raise _WorkspaceIntegrityError("Bug Lab incident record time fields are invalid")
        if not all(
            self._is_optional_number(record[name]) for name in ("detect_s", "resolve_s")
        ):
            raise _WorkspaceIntegrityError("Bug Lab incident record time fields are invalid")
        if (
            not isinstance(record["version_at_open"], str)
            or not isinstance(record["backend"], dict)
            or (
                record["diagnosis_matched"] is not None
                and type(record["diagnosis_matched"]) is not bool
            )
        ):
            raise _WorkspaceIntegrityError("Bug Lab incident record shape is invalid")
        for name in ("injected", "alerts", "attempts", "events"):
            if not isinstance(record[name], list) or not all(
                isinstance(item, dict) for item in record[name]
            ):
                raise _WorkspaceIntegrityError("Bug Lab incident record shape is invalid")
        if not all(
            isinstance(item.get("name"), str) for item in record["injected"]
        ):
            raise _WorkspaceIntegrityError("Bug Lab incident history is invalid")
        for name in ("diagnosis", "patch", "approval", "verification"):
            if record[name] is not None and not isinstance(record[name], dict):
                raise _WorkspaceIntegrityError("Bug Lab incident record shape is invalid")
        interrupted_state = record.get("interrupted_state")
        if interrupted_state is not None:
            interrupted_fields = {
                "status", "stage", "approval", "verification", "resolved_by",
                "resolve_s", "summary", "outcome",
            }
            if (
                not isinstance(interrupted_state, dict)
                or not interrupted_fields.issubset(interrupted_state)
                or not isinstance(interrupted_state["status"], str)
                or not isinstance(interrupted_state["stage"], str)
                or not all(
                    interrupted_state[name] is None
                    or isinstance(interrupted_state[name], dict)
                    for name in ("approval", "verification")
                )
                or (
                    interrupted_state["resolved_by"] is not None
                    and not isinstance(interrupted_state["resolved_by"], str)
                )
                or not self._is_optional_number(interrupted_state["resolve_s"])
                or any(
                    interrupted_state[name] is not None
                    and not isinstance(interrupted_state[name], str)
                    for name in ("summary", "outcome")
                )
            ):
                raise _WorkspaceIntegrityError("Bug Lab interrupted state is invalid")
        try:
            _serialize_json(record)
        except _WorkspaceInputError:
            raise _WorkspaceIntegrityError("Bug Lab incident record is not serializable") from None
        if terminal and not self._is_terminal(record):
            raise _WorkspaceIntegrityError("Bug Lab journal contains a nonterminal record")

    @staticmethod
    def _is_number(value: Any) -> bool:
        return type(value) is int or type(value) is float and math.isfinite(value)

    @classmethod
    def _is_optional_number(cls, value: Any) -> bool:
        return value is None or cls._is_number(value)

    @staticmethod
    def _is_terminal(record: dict[str, Any]) -> bool:
        active_statuses = {"open", "awaiting_approval", "active", "pending"}
        closed_at = record.get("closed_at")
        return (
            isinstance(record.get("status"), str)
            and record["status"] not in active_statuses
            and record.get("stage") == "closed"
            and type(closed_at) in (int, float)
        )

    def _normalize_record(self, record: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(record, dict):
            raise _WorkspaceInputError("Incident record must be an object")
        normalized = copy.deepcopy(record)
        sequence = normalized.get("sequence")
        incident_id = normalized.get("id")
        if sequence is None and isinstance(incident_id, str) and incident_id.startswith("INC-"):
            suffix = incident_id[4:]
            if suffix.isdecimal():
                sequence = int(suffix)
        if type(sequence) is not int or sequence < 1:
            raise _WorkspaceInputError("Incident record needs a positive sequence")
        expected_id = f"INC-{sequence:04d}"
        if incident_id is not None and incident_id != expected_id:
            raise _WorkspaceInputError("Incident record id does not match its sequence")
        normalized["id"] = expected_id
        normalized["sequence"] = sequence
        identity = normalized.get("workspace_id")
        if identity is not None and identity != self.workspace_id:
            raise _WorkspaceInputError("Incident record belongs to a different workspace")
        normalized["workspace_id"] = self.workspace_id
        schema = normalized.get("evidence_schema_version", _EVIDENCE_SCHEMA_VERSION)
        classification = normalized.get(
            "evidence_classification",
            _EVIDENCE_CLASSIFICATION,
        )
        if type(schema) is not int or schema != _EVIDENCE_SCHEMA_VERSION:
            raise _WorkspaceInputError("Incident record schema version is invalid")
        if classification != _EVIDENCE_CLASSIFICATION:
            raise _WorkspaceInputError("Incident record classification is invalid")
        normalized["evidence_schema_version"] = _EVIDENCE_SCHEMA_VERSION
        normalized["evidence_classification"] = _EVIDENCE_CLASSIFICATION
        _serialize_json(normalized)
        try:
            self._validate_record(normalized)
        except _WorkspaceIntegrityError as exc:
            raise _WorkspaceInputError(str(exc)) from None
        return normalized

    def _load_journal(self):
        raw = self._read_owned_file(self.log_path) or b""
        if not raw:
            return [], None, b""
        complete_end = len(raw) if raw.endswith(b"\n") else raw.rfind(b"\n") + 1
        prefix = raw[:complete_end]
        tail = raw[complete_end:] or None
        records = []
        if prefix:
            for line in prefix.split(b"\n")[:-1]:
                if not line:
                    raise _WorkspaceIntegrityError("Bug Lab journal contains an empty record")
                record = _load_json_object(line)
                self._validate_record(record, terminal=True)
                if record["sequence"] != len(records) + 1:
                    raise _WorkspaceIntegrityError("Bug Lab journal sequence is invalid")
                records.append(record)
        return records, tail, prefix

    def _load_pending(self) -> dict[str, Any] | None:
        raw = self._read_owned_file(self._pending_path)
        if raw is None:
            return None
        pending = _load_json_object(raw)
        self._validate_record(pending)
        return pending

    def _rewrite_partial_journal(
        self,
        prefix: bytes,
        tail: bytes,
        pending: dict[str, Any],
    ) -> None:
        payload = _serialize_json(pending) + b"\n"
        if not payload.startswith(tail):
            raise _WorkspaceIntegrityError("Bug Lab journal tail has no matching recovery proof")
        fd = self._open_regular_file(self.log_path, os.O_RDWR)
        try:
            existing = _read_all(fd)
            if existing != prefix + tail:
                raise _WorkspaceIntegrityError("Bug Lab journal changed during recovery")
            os.ftruncate(fd, len(prefix))
            os.lseek(fd, len(prefix), os.SEEK_SET)
            _write_all(fd, payload)
            os.fsync(fd)
        finally:
            os.close(fd)
        _sync_directory(self.root)

    def _append_record(self, record: dict[str, Any]) -> None:
        records, tail, prefix = self._load_journal()
        if tail is not None:
            raise _WorkspaceIntegrityError("Bug Lab journal has an unreconciled partial record")
        expected_sequence = records[-1]["sequence"] + 1 if records else 1
        if record["sequence"] != expected_sequence:
            raise _WorkspaceIntegrityError("Bug Lab journal sequence is invalid")
        fd = self._open_regular_file(
            self.log_path,
            os.O_RDWR | os.O_APPEND | os.O_CREAT,
        )
        try:
            existing = _read_all(fd)
            if existing != prefix:
                raise _WorkspaceIntegrityError("Bug Lab journal changed during append")
            _write_all(fd, _serialize_json(record) + b"\n")
            os.fsync(fd)
        finally:
            os.close(fd)
        _sync_directory(self.root)

    def _write_pending(self, record: dict[str, Any]) -> None:
        self._atomic_write_raw(
            self._pending_path,
            _serialize_json(record) + b"\n",
        )
        self._pending_record = copy.deepcopy(record)
        self._sequence = max(self._sequence, record["sequence"])

    def _remove_pending(self) -> None:
        if self._file_stat(self._pending_path) is None:
            self._pending_record = None
            return
        self._file_stat(self._pending_path, required=True)
        os.unlink(self._pending_path)
        _sync_directory(self.root)
        self._pending_record = None

    def _interrupted(self, record: dict[str, Any]) -> dict[str, Any]:
        interrupted = copy.deepcopy(record)
        interrupted["interrupted_state"] = {
            key: copy.deepcopy(record.get(key))
            for key in (
                "status", "stage", "approval", "verification", "resolved_by",
                "resolve_s", "summary", "outcome",
            )
        }
        interrupted["status"] = "interrupted"
        interrupted["stage"] = "closed"
        interrupted["active_agent"] = None
        interrupted["active_backend"] = None
        interrupted["closed_at"] = time.time()
        interrupted["outcome"] = "Interrupted before completion; workflow was not replayed."
        interrupted["summary"] = "Interrupted before completion; no workflow was replayed."
        interrupted["verification"] = None
        interrupted["resolved_by"] = None
        interrupted["resolve_s"] = None
        return interrupted

    def _open_existing(self) -> None:
        self._validate_layout(scan_site=True)
        self._workspace_id = self._read_marker()
        records, tail, prefix = self._load_journal()
        pending = self._load_pending()

        if tail is not None:
            if pending is None or not self._is_terminal(pending):
                raise _WorkspaceIntegrityError("Bug Lab partial journal has no terminal recovery proof")
            next_sequence = records[-1]["sequence"] + 1 if records else 1
            if pending["sequence"] != next_sequence:
                raise _WorkspaceIntegrityError("Bug Lab pending sequence is invalid")
            self._rewrite_partial_journal(prefix, tail, pending)
            records.append(copy.deepcopy(pending))

        if pending is not None:
            last_sequence = records[-1]["sequence"] if records else 0
            if pending["sequence"] == last_sequence:
                if not self._is_terminal(pending) or records[-1] != pending:
                    raise _WorkspaceIntegrityError("Bug Lab pending record conflicts with its journal")
                self._remove_pending()
            elif pending["sequence"] == last_sequence + 1:
                if not self._is_terminal(pending):
                    pending = self._interrupted(pending)
                    self._write_pending(pending)
                self._append_record(pending)
                records.append(copy.deepcopy(pending))
                self._remove_pending()
            else:
                raise _WorkspaceIntegrityError("Bug Lab pending sequence is invalid")

        self._records = records
        self._sequence = records[-1]["sequence"] if records else 0
        self._pending_record = None

    def _require_writable(self) -> None:
        if (
            not self._healthy
            or self._closed
            or self._lock_fd is None
            or self._lock_finalizer is None
            or not self._lock_finalizer.alive
        ):
            raise WorkspacePersistenceError(_PERSISTENCE_ERROR)

    def _validate_live_workspace(self, *, require_site: bool = True) -> None:
        self._validate_layout(require_site=require_site)
        if self._read_marker() != self.workspace_id:
            raise _WorkspaceIntegrityError("Bug Lab workspace marker changed")

    def reset(self) -> None:
        self._require_writable()
        try:
            self._validate_live_workspace(require_site=False)
            if not self.site.exists():
                shutil.copytree(self.source, self.site)
            else:
                self._validate_site_tree()
                for name in EDITABLE_FILES:
                    self.write(name, self.pristine(name))
        except _WorkspaceIntegrityError:
            self._healthy = False
            raise _WorkspaceIntegrityError("Bug Lab reset must stay inside its owned workspace") from None
        except (OSError, shutil.Error):
            self._healthy = False
            raise WorkspacePersistenceError(_PERSISTENCE_ERROR) from None

    def _editable(self, name: str) -> Path:
        if name not in EDITABLE_FILES:
            raise ValueError(f"{name!r} is not an editable store file")
        return self.site / name

    def read(self, name: str) -> str:
        path = self._editable(name)
        self._directory_stat(self.site)
        raw = self._read_owned_file(path, required=True)
        try:
            return raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        except UnicodeDecodeError:
            raise ValueError("Bug Lab store file is not valid UTF-8") from None

    def pristine(self, name: str) -> str:
        if name not in EDITABLE_FILES:
            raise ValueError(f"{name!r} is not an editable store file")
        return (self.source / name).read_text(encoding="utf-8")

    def write(self, name: str, text: str) -> None:
        self._require_writable()
        path = self._editable(name)
        if not isinstance(text, str):
            raise _WorkspaceInputError("Bug Lab store content must be text")
        try:
            self._validate_live_workspace()
            self._directory_stat(self.site)
            self._atomic_write_raw(path, text.encode("utf-8"))
        except _WorkspaceIntegrityError:
            self._healthy = False
            raise
        except (OSError, shutil.Error):
            self._healthy = False
            raise WorkspacePersistenceError(_PERSISTENCE_ERROR) from None

    def changed_files(self) -> list[str]:
        return [name for name in EDITABLE_FILES if self.read(name) != self.pristine(name)]

    def public_path(self, relative: str) -> Path | None:
        """Resolve a URL path to a served file, refusing anything outside the store."""
        if not relative or relative.endswith("/"):
            relative = f"{relative}index.html"
        parts = Path(relative).parts
        if any(part in {"..", ""} or part.startswith(".") for part in parts):
            return None
        candidate = self.site / relative
        try:
            self._directory_stat(self.site)
            if has_redirecting_path_component(candidate):
                return None
            resolved = candidate.resolve()
            if (
                not resolved.is_relative_to(self.site)
                or not resolved.is_file()
                or self._file_stat(resolved) is None
            ):
                return None
        except (OSError, _WorkspaceIntegrityError):
            return None
        return resolved

    def public_content(self, relative: str) -> tuple[Path, bytes] | None:
        path = self.public_path(relative)
        if path is None:
            return None
        try:
            self._directory_stat(self.site)
            return path, self._read_owned_file(path, required=True)
        except (OSError, _WorkspaceIntegrityError):
            return None

    def evidence_bytes(self) -> bytes:
        try:
            self._directory_stat(self.root)
            return self._read_owned_file(self.log_path) or b""
        except (OSError, _WorkspaceIntegrityError):
            self._healthy = False
            raise WorkspacePersistenceError(_PERSISTENCE_ERROR) from None

    def asset_exists(self, relative: str) -> bool:
        return self.public_path(relative) is not None

    def files(self) -> list[str]:
        self._validate_site_tree()
        return sorted(
            path.relative_to(self.site).as_posix()
            for path in self.site.rglob("*")
            if path.is_file() and not path.name.startswith(".")
        )

    def version(self) -> str:
        digest = hashlib.sha256()
        for name in self.files():
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(self._read_owned_file(self.site / name, required=True) + b"\0")
        return digest.hexdigest()[:16]

    def checkpoint(self, record: dict[str, Any]) -> None:
        self._require_writable()
        normalized = self._normalize_record(record)
        try:
            self._validate_live_workspace()
            pending = self._load_pending()
            if pending is not None:
                if pending["sequence"] != normalized["sequence"]:
                    raise _WorkspaceIntegrityError("Bug Lab already has a different pending incident")
            elif normalized["sequence"] != self._sequence + 1:
                raise _WorkspaceInputError("Incident sequence is not the next workspace sequence")
            self._write_pending(normalized)
        except _WorkspaceInputError:
            raise
        except _WorkspaceIntegrityError:
            self._healthy = False
            raise
        except (OSError, shutil.Error):
            self._healthy = False
            raise WorkspacePersistenceError(_PERSISTENCE_ERROR) from None

    def complete(self, record: dict[str, Any]) -> None:
        self._require_writable()
        normalized = self._normalize_record(record)
        if not self._is_terminal(normalized):
            raise _WorkspaceInputError("Incident record must be terminal before completion")
        try:
            self._validate_live_workspace()
            records, tail, prefix = self._load_journal()
            pending = self._load_pending()
            existing = next(
                (item for item in records if item["sequence"] == normalized["sequence"]),
                None,
            )
            if existing is not None and tail is None:
                if existing != normalized:
                    raise _WorkspaceIntegrityError("Bug Lab completed record conflicts with the journal")
                if pending is not None:
                    if pending != existing:
                        raise _WorkspaceIntegrityError("Bug Lab pending record conflicts with the journal")
                    self._remove_pending()
                self._records = records
                self._sequence = records[-1]["sequence"] if records else 0
                return
            if pending is not None and pending["sequence"] != normalized["sequence"]:
                raise _WorkspaceIntegrityError("Bug Lab already has a different pending incident")
            last_sequence = records[-1]["sequence"] if records else 0
            if existing is None and normalized["sequence"] != last_sequence + 1:
                raise _WorkspaceIntegrityError("Bug Lab journal sequence is invalid")

            self._write_pending(normalized)
            records, tail, prefix = self._load_journal()
            if tail is not None:
                self._rewrite_partial_journal(prefix, tail, normalized)
                records.append(copy.deepcopy(normalized))
            else:
                existing = next(
                    (item for item in records if item["sequence"] == normalized["sequence"]),
                    None,
                )
                if existing is None:
                    self._append_record(normalized)
                    records.append(copy.deepcopy(normalized))
                elif existing != normalized:
                    raise _WorkspaceIntegrityError("Bug Lab completed record conflicts with the journal")
            self._remove_pending()
            self._records = records
            self._sequence = records[-1]["sequence"] if records else 0
        except _WorkspaceIntegrityError:
            self._healthy = False
            raise
        except (OSError, shutil.Error):
            self._healthy = False
            raise WorkspacePersistenceError(_PERSISTENCE_ERROR) from None

    def recovery(self, history_limit: int = 50) -> Recovery:
        self._require_writable()
        if type(history_limit) is not int or history_limit < 0:
            raise ValueError("history_limit must be a non-negative integer")
        try:
            self._validate_live_workspace()
        except _WorkspaceIntegrityError:
            self._healthy = False
            raise
        except (OSError, shutil.Error):
            self._healthy = False
            raise WorkspacePersistenceError(_PERSISTENCE_ERROR) from None
        recent = self._records[-history_limit:] if history_limit else []
        selected = tuple(copy.deepcopy(record) for record in reversed(recent))
        return Recovery(self.workspace_id, self._sequence, selected)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        self._healthy = False
        if self._lock_finalizer is not None and self._lock_finalizer.alive:
            self._lock_finalizer()
        self._lock_fd = None


def unified_diff(name: str, before: str, after: str) -> list[str]:
    return list(
        difflib.unified_diff(
            before.splitlines(), after.splitlines(),
            fromfile=f"a/{name}", tofile=f"b/{name}", lineterm="", n=2,
        )
    )
