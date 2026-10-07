"""Opt-in loopback controls for one startup-configured, governed Stage 4 run."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal

import httpx
from pydantic import BaseModel, ConfigDict, field_validator

from agents.tool_policy import AGENT_EXPOSED_TOOLS, CLUSTER_MUTATING_TOOLS
from config.g4_launch_authority import LOCAL_OPERATOR_AUTHORITY
from demo.incident_monitor import _object, _safe_file, read_incident


class OperatorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    checkout: Path
    capture_root: Path
    secret_dir: Path
    inference_config: Path
    attempt_ledger_root: Path
    attempt_ledger_inventory_sha256: str
    source_sha: str
    protocol_fingerprint: str
    protocol_profile: Literal["historical", "website-demo-candidate"] = "historical"
    experiment_id: str
    approved_by: str

    @field_validator("checkout", "capture_root", "secret_dir", "inference_config", "attempt_ledger_root")
    @classmethod
    def absolute_path(cls, value: Path) -> Path:
        if not value.is_absolute() or ".." in value.parts:
            raise ValueError("Explicit absolute paths are required")
        return value

    @field_validator("source_sha", "protocol_fingerprint", "attempt_ledger_inventory_sha256")
    @classmethod
    def digest(cls, value: str, info) -> str:
        size = 40 if info.field_name == "source_sha" else 64
        if not re.fullmatch(rf"[0-9a-f]{{{size}}}", value):
            raise ValueError("Exact lowercase source/protocol digest required")
        return value

    @field_validator("experiment_id")
    @classmethod
    def experiment(cls, value: str) -> str:
        if not re.fullmatch(r"EXP-STAGE4-SF002-\d{3}", value):
            raise ValueError("Only the governed SF002 runner is supported")
        return value

    @field_validator("approved_by")
    @classmethod
    def operator(cls, value: str) -> str:
        if not value.strip() or len(value) > 120:
            raise ValueError("Explicit operator identity required")
        return value.strip()


def load_config(path: Path) -> OperatorConfig:
    data, _ = _object(path.parent, path.name, 16384)
    return OperatorConfig.model_validate(data)


def _write_new(path: Path, value: dict) -> None:
    with path.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, allow_nan=False)
        stream.flush()
        os.fsync(stream.fileno())


class OperatorRun:
    """One launch per server lifetime; the runner remains the reservation authority."""

    def __init__(self, config: OperatorConfig):
        if config.capture_root.resolve().is_relative_to(config.checkout.resolve()):
            raise ValueError("Operator captures must be outside the execution checkout")
        self.config = config
        self.csrf = secrets.token_urlsafe(32)
        self._lock = threading.Lock()
        self._process: subprocess.Popen | None = None
        self._capture = config.capture_root / "operator"
        self._launched = self._capture.exists()
        self._launch_claim = f"{config.protocol_fingerprint}.json"
        self._worker: threading.Thread | None = None
        self._capture_error = False
        self._start_event = threading.Event()

    def _git(self, *args: str) -> str:
        result = subprocess.run(
            ["git", "-C", str(self.config.checkout), *args],
            capture_output=True, text=True, timeout=10, check=True,
            env={**os.environ, "GIT_TERMINAL_PROMPT": "0"},
        )
        return result.stdout.strip()

    def readiness(self) -> dict:
        """No cluster, inference, reservation, or resource writes in this precheck."""
        from config.g4_protocol import (
            agent_prompt_hashes,
            causal_evidence_policy_profile,
            protocol_fingerprint,
        )
        from config.g4_protocol_selection import declared_profile
        from scripts.run_stage4_golden_incident import (
            MAX_ATTEMPTS_PER_PROTOCOL_MARKER,
            _claimed_attempts_for_protocol_fingerprint,
        )

        blockers = []
        if self._capture.exists():
            blockers.append("capture_already_exists")
        used = None
        profile = declared_profile(self.config.protocol_profile)
        fingerprint = protocol_fingerprint(profile)
        attempt_limit = (
            profile["website_demo"]["maximum_website_launches"]
            if self.config.protocol_profile == "website-demo-candidate"
            else MAX_ATTEMPTS_PER_PROTOCOL_MARKER
        )
        try:
            if Path(__file__).resolve().parents[1] != self.config.checkout.resolve():
                blockers.append("operator_source_mismatch")
            if self._git("rev-parse", "HEAD") != self.config.source_sha:
                blockers.append("source_sha_mismatch")
            if self._git("status", "--porcelain", "--untracked-files=no"):
                blockers.append("tracked_source_dirty")
            if self._git("status", "--porcelain", "--untracked-files=all"):
                blockers.append("source_not_clean")
            if self._git("symbolic-ref", "--quiet", "--short", "HEAD") != "main":
                blockers.append("execution_branch_must_be_main")
            if self._git("rev-parse", "--verify", "origin/main^{commit}") != self.config.source_sha:
                blockers.append("origin_main_mismatch")
            if self.config.protocol_fingerprint != fingerprint:
                blockers.append("protocol_fingerprint_mismatch")
            if agent_prompt_hashes() != profile["agent_prompt_sha256"]:
                blockers.append("prompt_profile_mismatch")
            if self.config.protocol_profile == "website-demo-candidate":
                from config.g4_demo_candidate import inspect_candidate

                if inspect_candidate()["status"] != "PREPARED":
                    blockers.append("candidate_source_profile_mismatch")
            elif causal_evidence_policy_profile() != profile["causal_evidence_policy"]:
                blockers.append("causal_source_profile_mismatch")
            used = _claimed_attempts_for_protocol_fingerprint(
                fingerprint, str(self.config.checkout),
            )
            historical = self.config.attempt_ledger_root / "artifacts/evidence/stage4/.attempts"
            if not historical.is_dir():
                blockers.append("preserved_attempt_ledger_unavailable")
            else:
                if self.config.attempt_ledger_root.resolve() == self.config.checkout.resolve():
                    blockers.append("preserved_ledger_must_be_independent")
                preserved_used = _claimed_attempts_for_protocol_fingerprint(
                    fingerprint, str(self.config.attempt_ledger_root),
                )
                used = max(used, preserved_used)
                markers = sorted(historical.glob("*.attempt.json"))
                if not markers or len(markers) > 200:
                    raise ValueError("Attempt ledger exceeds bound")
                inventory = {}
                for marker in markers:
                    relative = f"artifacts/evidence/stage4/.attempts/{marker.name}"
                    preserved = _safe_file(self.config.attempt_ledger_root, relative, 1024**2)
                    current = _safe_file(self.config.checkout, relative, 1024**2)
                    inventory[marker.name] = hashlib.sha256(preserved or b"").hexdigest()
                    if not preserved or current != preserved:
                        blockers.append("preserved_attempt_ledger_mismatch")
                poison = _safe_file(
                    self.config.attempt_ledger_root,
                    "artifacts/evidence/stage4/.poisoned-environment.json", 1024**2,
                )
                if poison is not None:
                    blockers.append("preserved_environment_poisoned")
                    inventory[".poisoned-environment.json"] = hashlib.sha256(poison).hexdigest()
                inventory_digest = hashlib.sha256(json.dumps(
                    inventory, sort_keys=True, separators=(",", ":"),
                ).encode()).hexdigest()
                if inventory_digest != self.config.attempt_ledger_inventory_sha256:
                    blockers.append("preserved_ledger_inventory_mismatch")
            if used >= attempt_limit:
                blockers.append("protocol_attempt_budget_exhausted")
            evidence = self.config.checkout / "artifacts/evidence/stage4"
            if any((evidence / suffix).exists() for suffix in (
                f"{self.config.experiment_id}.json",
                f".attempts/{self.config.experiment_id}.attempt.json",
                ".poisoned-environment.json",
            )):
                blockers.append("attempt_exists_or_environment_poisoned")
            if not LOCAL_OPERATOR_AUTHORITY.is_absolute():
                blockers.append("local_operator_authority_unavailable")
            elif _safe_file(LOCAL_OPERATOR_AUTHORITY, self._launch_claim, 16384) is not None:
                blockers.append("protocol_website_launch_already_claimed")
            for path in (self.config.inference_config, self.config.secret_dir / "atlasops-api-key.secret"):
                # Inspect only bounded regular-file metadata here, not secret values.
                if not path.is_file() or path.is_symlink():
                    blockers.append("private_runtime_config_unavailable")
            if self.config.secret_dir.resolve().is_relative_to(self.config.checkout.resolve()):
                blockers.append("secrets_must_be_outside_checkout")
        except (OSError, ValueError, RuntimeError, subprocess.SubprocessError):
            blockers.append("source_or_ledger_unavailable")
        if self._launched:
            blockers.append("server_launch_already_used")
        return {
            "can_start": not blockers, "blockers": list(dict.fromkeys(blockers)),
            "attempts_used": used, "attempt_limit": attempt_limit,
            "runtime_qualified": False,
        }

    def _finish(self, output, error):
        self._start_event.wait()
        process = self._process
        code = process.wait() if process is not None else -1
        output.close()
        error.close()
        try:
            _write_new(self._capture / "exit.json", {
                "exit_code": code, "finished_at_utc": datetime.now(UTC).isoformat(),
            })
        except OSError:
            self._capture_error = True

    def start(self, supplied_token: str) -> dict:
        with self._lock:
            self.authenticate(supplied_token)
            status = self.readiness()
            if not status["can_start"]:
                raise ValueError("Governed launch blocked: " + ", ".join(status["blockers"]))
            remote = self._git("ls-remote", "--exit-code", "origin", "refs/heads/main")
            if remote.split() != [self.config.source_sha, "refs/heads/main"]:
                raise ValueError("Remote source freshness mismatch")
            if not LOCAL_OPERATOR_AUTHORITY.is_absolute():
                raise ValueError("Local operator authority unavailable")
            claim_path = LOCAL_OPERATOR_AUTHORITY / self._launch_claim
            claim_path.parent.mkdir(parents=True, exist_ok=True)
            _safe_file(LOCAL_OPERATOR_AUTHORITY, self._launch_claim, 16384)
            launch_token = secrets.token_urlsafe(32)
            # Fixed machine authority, independent of configurable/copyable ledgers.
            _write_new(claim_path, {
                "experiment_id": self.config.experiment_id,
                "source_sha": self.config.source_sha,
                "protocol_fingerprint": self.config.protocol_fingerprint,
                "channel_path": str(self._capture / "channel.json"),
                "launch_token_sha256": hashlib.sha256(launch_token.encode()).hexdigest(),
                "claimed_at": datetime.now(UTC).isoformat(),
            })
            self._capture.mkdir(parents=True, exist_ok=False)
            env = os.environ.copy()
            env.update({
                "STAGE4_APPROVED_MAIN_SHA": self.config.source_sha,
                "ATLASOPS_STAGE4_PROTOCOL": self.config.protocol_profile,
                "STAGE4_APPROVED_PROTOCOL_SHA256": self.config.protocol_fingerprint,
                "ATLASOPS_STAGE4_LAUNCH_TOKEN": launch_token,
                "STAGE4_EXPERIMENT_ID": self.config.experiment_id,
                "ATLASOPS_STAGE4_SECRET_DIR": str(self.config.secret_dir),
                "ATLASOPS_INFERENCE_CONFIG": str(self.config.inference_config),
                "ATLASOPS_RUNTIME_DATA_DIR": str(self.config.checkout / "data"),
                "ATLASOPS_RECOMMENDER_ENABLED": "0",
                "KUBECONFIG_CONTEXT": "kind-atlasops-local",
                "ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE": str(self._capture / "channel.json"),
                "PYTHONUNBUFFERED": "1",
            })
            output = (self._capture / "stdout.log").open("xb")
            error = (self._capture / "stderr.log").open("xb")
            self._launched = True
            # Establish the lifecycle worker before any child can start.
            self._worker = threading.Thread(target=self._finish, args=(output, error), daemon=True)
            try:
                self._worker.start()
            except RuntimeError:
                output.close()
                error.close()
                raise ValueError("Lifecycle worker could not start; no runner launched") from None
            try:
                process = subprocess.Popen(
                    [sys.executable, "-B", "-m", "scripts.run_stage4_golden_incident"],
                    cwd=self.config.checkout, env=env, stdout=output, stderr=error,
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
            except OSError:
                self._start_event.set()
                raise ValueError("Runner process could not start") from None
            self._process = process
            self._start_event.set()
            try:
                _write_new(self._capture / "launch.json", {
                    "pid": process.pid, "mode": "golden", "source_sha": self.config.source_sha,
                    "started_at_utc": datetime.now(UTC).isoformat(),
                })
            except OSError:
                self._capture_error = True
            return {
                "started": True, "experiment_id": self.config.experiment_id,
                "capture_error": self._capture_error,
            }

    def authenticate(self, token: str) -> None:
        if not token or not hmac.compare_digest(token, self.csrf):
            raise PermissionError("Invalid operator session")

    def _channel(self) -> tuple[str, str]:
        if self._process is None or self._process.poll() is not None:
            raise ValueError("No active owned runner")
        channel, _ = _object(self._capture, "channel.json", 16384)
        if (
            channel.get("pid") != self._process.pid
            or channel.get("experiment_id") != self.config.experiment_id
            or channel.get("source_sha") != self.config.source_sha
            or not re.fullmatch(r"http://127\.0\.0\.1:[1-9]\d{0,4}", str(channel.get("url")))
        ):
            raise ValueError("Owned approval channel unavailable")
        port = int(channel["url"].rsplit(":", 1)[1])
        if port > 65535:
            raise ValueError("Invalid approval listener")
        raw = _safe_file(self.config.secret_dir, "atlasops-api-key.secret", 16384)
        key = raw.decode("utf-8").strip() if raw else ""
        if not key:
            raise ValueError("Operator credential unavailable")
        return channel["url"], key

    def _pending(self, client) -> list[dict]:
        response = client.get("/approval/pending")
        response.raise_for_status()
        if len(response.content) > 65536:
            raise ValueError("Approval response exceeds bound")
        data = response.json()["pending"]
        if not isinstance(data, list) or len(data) > 20:
            raise ValueError("Invalid approval response")
        expected_scope = {"kube_context": "kind-atlasops-local", "scenario_id": "single_fault/sf-002"}
        pending = []
        for record in data:
            if record.get("decision") != "pending" or record.get("operator_scope") != expected_scope:
                continue
            action = record.get("action")
            if (
                not isinstance(action, dict) or set(action) != {"tool", "arguments"}
                or action.get("tool") not in CLUSTER_MUTATING_TOOLS
            ):
                raise ValueError("Exact action unavailable")
            from agents.approval import ApprovalGate
            digest = ApprovalGate.action_digest(action)
            if digest != record.get("action_digest"):
                raise ValueError("Approval action digest mismatch")
            pending.append({
                "token": record["token"], "action": action, "action_digest": digest,
                "incident_id": record["incident_id"], "operator_scope": expected_scope,
                "severity": record["severity"],
            })
        return pending

    def pending(self) -> tuple[list[dict], str | None]:
        try:
            url, key = self._channel()
            with httpx.Client(base_url=url, headers={"X-AtlasOps-Key": key}, timeout=5, trust_env=False) as client:
                return self._pending(client), None
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
            return [], "approval_channel_unavailable"

    def decide(self, csrf: str, token: str, digest: str, decision: str) -> dict:
        with self._lock:
            self.authenticate(csrf)
            if decision not in {"approved", "rejected"}:
                raise ValueError("Only explicit approval or rejection is supported")
            url, key = self._channel()
            with httpx.Client(base_url=url, headers={"X-AtlasOps-Key": key}, timeout=5, trust_env=False) as client:
                matching = [p for p in self._pending(client) if p["token"] == token and p["action_digest"] == digest]
                if len(matching) != 1:
                    raise ValueError("Proposal is stale or does not match the exact action")
                response = client.post("/approve", json={
                    "token": token, "decision": decision, "approved_by": self.config.approved_by,
                    "reason": f"Website exact-action decision for digest {digest}",
                })
                response.raise_for_status()
                if response.json().get("ok") is not True:
                    raise ValueError("Runner rejected the approval decision")
            return {"decision": decision, "action_digest": digest}

    def events(self) -> list[dict] | None:
        try:
            url, key = self._channel()
            with httpx.Client(base_url=url, headers={"X-AtlasOps-Key": key}, timeout=5, trust_env=False) as client:
                response = client.get("/operator/events")
                response.raise_for_status()
            if len(response.content) > 65536:
                raise ValueError("Activity response exceeds bound")
            rows = response.json()["events"]
            if not isinstance(rows, list) or len(rows) > 200:
                raise ValueError("Invalid activity response")
            return [{
                "role": row["role"], "phase": row["phase"],
                "tool": row.get("tool") if row.get("tool") in AGENT_EXPOSED_TOOLS else None,
            } for row in rows if (
                isinstance(row, dict)
                and row.get("role") in {"triage", "diagnosis", "remediation", "comms"}
                and row.get("phase") in {
                    "thinking", "tool_call", "tool_result", "waiting_approval", "conclusion",
                }
            )]
        except (OSError, ValueError, KeyError, TypeError, httpx.HTTPError):
            return None

    def snapshot(self) -> dict:
        pending, channel_error = self.pending()
        capture = None
        if self._launched and self._capture.exists():
            capture = read_incident(
                self.config.capture_root, self.config.experiment_id, "operator",
                execution_root=self.config.checkout,
            )
            if self._process is not None:
                capture["process_running"] = self._process.poll() is None
            if self._capture_error:
                capture["status"] = "CAPTURE_ERROR / ACTIVITY_UNVERIFIED"
        return {
            "enabled": True, "csrf_token": self.csrf,
            "experiment_id": self.config.experiment_id,
            "source_sha": self.config.source_sha,
            "protocol_fingerprint": self.config.protocol_fingerprint,
            "protocol_profile": self.config.protocol_profile,
            "scenario_id": "single_fault/sf-002", "kube_context": "kind-atlasops-local",
            "readiness": self.readiness(), "capture": capture,
            "pending": pending, "channel_error": channel_error, "events": self.events(),
            "capture_error": self._capture_error,
        }
