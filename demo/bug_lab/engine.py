"""The bug lab's incident pipeline.

Monitor -> Triage -> Diagnosis -> Patch -> Approval gate -> Apply -> Verifier -> Comms

The verifier, not an agent, decides whether an incident is resolved: every
server check and a fresh browser report for the patched version must pass.
A failed fix is reverted. After the planned attempts, a labelled rollback
policy (not AI) restores the last known-good release.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from agents.approval import approval_mode_for_severity
from demo.bug_lab import agents
from demo.bug_lab.catalog import BUGS, BUGS_BY_ID
from demo.bug_lab.checks import (
    SLOW_API_MS, Check, all_pass, browser_checks, failing, server_checks,
)
from demo.bug_lab.llm import Backend
from demo.bug_lab.workspace import Workspace, unified_diff

log = logging.getLogger("atlasops.bug_lab")

STAGES = (
    ("monitor", "Monitor"),
    ("triage", "Triage agent"),
    ("diagnosis", "Diagnosis agent"),
    ("propose", "Remediation agent"),
    ("approval", "Approval gate"),
    ("apply", "Remediation agent"),
    ("verification", "Verifier"),
    ("comms", "Comms agent"),
)
_AGENT = dict(STAGES) | {"rollback": "Rollback policy"}


class LabBusy(RuntimeError):
    pass


class ApprovalError(ValueError):
    pass


@dataclass
class LabOptions:
    monitor_interval: float = 2.0
    step_delay: float = 0.6
    approval_timeout: float = 300.0
    verify_timeout: float = 25.0
    slow_api_ms: int = SLOW_API_MS
    history_limit: int = 50


@dataclass
class Change:
    kind: str  # patch or rollback
    author: str
    explanation: str
    files: dict[str, tuple[str, str]] = field(default_factory=dict)  # name -> (before, after)

    def digest(self, incident_id: str) -> str:
        canonical = json.dumps({
            "incident": incident_id, "kind": self.kind, "author": self.author,
            "explanation": self.explanation,
            "files": {name: [hashlib.sha256(before.encode()).hexdigest(),
                             hashlib.sha256(after.encode()).hexdigest()]
                      for name, (before, after) in sorted(self.files.items())},
        }, sort_keys=True)
        return hashlib.sha256(canonical.encode("utf-8")).hexdigest()

    def diff(self) -> list[str]:
        lines: list[str] = []
        for name, (before, after) in self.files.items():
            lines += unified_diff(name, before, after)
        return lines


ClientFactory = Callable[[], httpx.AsyncClient]


class BugLab:
    def __init__(self, workspace: Workspace, backend: Backend, client_factory: ClientFactory,
                 options: LabOptions | None = None, log_path: Path | None = None) -> None:
        self.workspace = workspace
        self.backend = backend
        self.client_factory = client_factory
        self.options = options or LabOptions()
        self.log_path = log_path
        self.require_approval_all = False
        self.injected: list[dict[str, Any]] = []
        self.incident: dict[str, Any] | None = None
        self.history: list[dict[str, Any]] = []
        self.server: list[Check] = []
        self.server_version: str | None = None
        self.browser: dict[str, dict[str, Any]] = {}
        self.latest_browser: dict[str, Any] | None = None
        self.suppressed_version: str | None = None
        self._approval: dict[str, Any] | None = None
        self._last_checks: list[Check] = []
        self._counter = 0
        self._wake = asyncio.Event()
        self._report = asyncio.Event()
        self._monitor_task: asyncio.Task[None] | None = None
        self._pipeline: asyncio.Task[None] | None = None

    # --- lifecycle -----------------------------------------------------

    async def start(self) -> None:
        self._monitor_task = asyncio.create_task(self._monitor_loop())

    async def stop(self) -> None:
        for task in (self._pipeline, self._monitor_task):
            if task and not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task

    @property
    def active(self) -> bool:
        return bool(self.incident) and self.incident["closed_at"] is None

    # --- inputs from the control room and the store page -----------------

    def inject(self, bug_id: str) -> dict[str, Any]:
        if bug_id not in BUGS_BY_ID:
            raise ValueError(f"Unknown bug {bug_id!r}")
        if self.active:
            raise LabBusy("An incident is in progress. Wait for it to close before injecting another bug.")
        if self.injected:
            raise LabBusy("The monitor has not detected the last bug yet.")
        bug = BUGS_BY_ID[bug_id]
        before = self.workspace.read(bug.file)
        try:
            after = bug.mutate(before)
        except ValueError as exc:
            raise LabBusy(f"{bug.name} cannot be injected into the current store: reset it first.") from exc
        self.workspace.write(bug.file, after)
        record = {**bug.public(), "at": time.time(), "diff": unified_diff(bug.file, before, after)}
        self.injected.append(record)
        self.suppressed_version = None
        # The monitor is not told about the injection; it finds the bug on its next cycle.
        return record

    async def reset(self) -> None:
        monitoring = bool(self._monitor_task and not self._monitor_task.done())
        await self.stop()
        if self.active:
            self._close(self.incident, "cancelled", "The store was reset by you.")
        self._approval = None
        self.workspace.reset()
        self.injected = []
        self.suppressed_version = None
        self._wake.set()
        if monitoring:
            await self.start()

    def record_browser_report(self, report: dict[str, Any]) -> None:
        version = report.get("version")
        if not isinstance(version, str) or not isinstance(report.get("checks"), dict):
            raise ValueError("A browser report needs a version and checks")
        issues = [
            {key: str(item.get(key, ""))[:400] for key in ("kind", "message", "source", "line")}
            for item in (report.get("issues") or [])[:40] if isinstance(item, dict)
        ]
        clean = {"version": version, "checks": report["checks"], "issues": issues,
                 "load_id": str(report.get("load_id", ""))[:40],
                 "page": str(report.get("page", ""))[:40], "received_at": time.time()}
        self.browser[version] = clean
        self.latest_browser = clean
        for stale in list(self.browser)[:-20]:
            del self.browser[stale]
        self._report.set()
        self._wake.set()

    def decide(self, incident_id: str, digest: str, decision: str) -> None:
        approval = self._approval
        if not approval or approval["incident_id"] != incident_id:
            raise ApprovalError("No approval is pending for this incident.")
        if approval["decision"]:
            raise ApprovalError("This approval was already decided.")
        if digest != approval["digest"]:
            raise ApprovalError("This decision is for a different change.")
        if time.time() > approval["expires_at"]:
            raise ApprovalError("This approval has expired.")
        if decision not in {"approve", "reject"}:
            raise ApprovalError("Decision must be approve or reject.")
        approval["decision"] = "approved" if decision == "approve" else "rejected"
        approval["event"].set()

    # --- monitor ---------------------------------------------------------

    async def run_server_checks(self) -> list[Check]:
        async with self.client_factory() as client:
            return await server_checks(client, slow_api_ms=self.options.slow_api_ms)

    async def monitor_once(self) -> None:
        version = self.workspace.version()
        checks = await self.run_server_checks()
        if self.workspace.version() != version:
            return
        self.server, self.server_version = checks, version
        if self.active or version == self.suppressed_version:
            return
        problems = failing(checks) + failing(browser_checks(self.browser.get(version),
                                                            slow_api_ms=self.options.slow_api_ms))
        if problems:
            self._open(problems, version)

    async def _monitor_loop(self) -> None:
        while True:
            self._wake.clear()
            try:
                await self.monitor_once()
            except Exception:  # keep monitoring; the failure is visible in the logs
                log.exception("monitor cycle failed")
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._wake.wait(), self.options.monitor_interval)

    # --- incident pipeline -------------------------------------------------

    def _open(self, problems: list[Check], version: str) -> None:
        self._counter += 1
        now = time.time()
        injected, self.injected = self.injected, []
        incident = {
            "id": f"INC-{self._counter:04d}", "status": "open", "stage": "monitor",
            "active_agent": "Monitor", "active_backend": "checks", "opened_at": now,
            "closed_at": None, "severity": None, "title": None, "injected": injected,
            "alerts": [check.public() for check in problems],
            "detect_s": round(now - min(item["at"] for item in injected), 1) if injected else None,
            "resolve_s": None, "diagnosis": None, "patch": None, "approval": None,
            "attempts": [], "events": [], "resolved_by": None, "outcome": None,
            "summary": None, "diagnosis_matched": None, "version_at_open": version,
        }
        self.incident = incident
        labels = ", ".join(check.label for check in problems)
        self._event(incident, "monitor", "result", f"Alert: {len(problems)} checks failing ({labels}).")
        self._pipeline = asyncio.create_task(self._run(incident, problems))

    async def _run(self, incident: dict[str, Any], problems: list[Check]) -> None:
        try:
            await self._triage(incident, problems)
            plans = ["model", "model", "rules"] if self.backend.mode == "llm" else ["rules"]
            feedback = ""
            for number, plan in enumerate(plans, 1):
                problems = await self._current_problems()
                if not problems:
                    incident["resolved_by"] = "No change needed"
                    break
                change, diagnosis = await self._plan(incident, problems, plan, feedback)
                if change is None:
                    feedback = "no usable patch was produced"
                    continue
                if not await self._gate(incident, change):
                    return
                self._apply(incident, change)
                try:
                    verdict = await self._verify(incident)
                except BaseException:
                    self._revert(incident, change)
                    raise
                incident["attempts"].append({"number": number, "author": change.author,
                                             "file": ", ".join(change.files), "verdict": verdict})
                if verdict == "verified":
                    incident["resolved_by"] = change.author
                    break
                if verdict == "no_browser":
                    self._close(incident, "unverified", "The fix was applied but no browser report "
                                "arrived to verify it. Keep the store page or the preview open.")
                    return
                self._revert(incident, change)
                feedback = (f"{change.author} changed {', '.join(change.files)} but these checks still "
                            f"failed: {', '.join(check.label for check in failing(self._last_checks))}")
            else:
                if not await self._rollback(incident):
                    return
            await self._comms(incident)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            log.exception("incident pipeline failed")
            self._event(incident, incident["stage"], "warning", f"Pipeline error: {exc}")
            self._close(incident, "escalated", f"Pipeline error: {exc}")

    async def _triage(self, incident: dict[str, Any], problems: list[Check]) -> None:
        self._stage(incident, "triage", "rule-based triage")
        severity, title, steps = agents.triage(problems)
        for step in steps:
            self._event(incident, "triage", step.kind, step.text, step.detail)
        incident["severity"], incident["title"] = severity, title
        mode = approval_mode_for_severity(severity)
        policy = "needs your approval" if mode != "auto" else "fixes run automatically"
        self._event(incident, "triage", "result", f"{severity}: {title}. Under AtlasOps policy, {severity} {policy}.")
        await self._pace()

    async def _current_problems(self) -> list[Check]:
        version = self.workspace.version()
        self._last_checks = await self.run_server_checks()
        return failing(self._last_checks) + failing(browser_checks(self.browser.get(version),
                                                                   slow_api_ms=self.options.slow_api_ms))

    async def _plan(self, incident: dict[str, Any], problems: list[Check], plan: str,
                    feedback: str) -> tuple[Change | None, agents.Diagnosis | None]:
        use_model = plan == "model"
        self._stage(incident, "diagnosis", self.backend.label if use_model else agents.RULES_AUTHOR)
        diagnosis = None
        if use_model:
            try:
                diagnosis = await agents.diagnose_llm(self.backend, problems, self.workspace, feedback)
            except Exception as exc:
                self._event(incident, "diagnosis", "warning", f"Model call failed: {exc}")
            if diagnosis is None:
                self._event(incident, "diagnosis", "warning",
                            "The model gave no usable diagnosis; using the rule engine.")
        rule_diagnosis = agents.diagnose_rules(problems, self.workspace)
        diagnosis = diagnosis or rule_diagnosis
        if diagnosis is None:
            self._event(incident, "diagnosis", "warning", "No diagnosis matches these signals.")
            return None, None
        for step in diagnosis.steps:
            self._event(incident, "diagnosis", step.kind, step.text, step.detail)
            await self._pace(0.5)
        incident["diagnosis"] = diagnosis.public()
        self._event(incident, "diagnosis", "result", f"Root cause ({diagnosis.author}): {diagnosis.root_cause}")
        await self._pace()

        model_diagnosed = use_model and diagnosis.author != agents.RULES_AUTHOR
        self._stage(incident, "propose", self.backend.label if model_diagnosed else agents.RULES_AUTHOR)
        patch = None
        if model_diagnosed:
            try:
                patch = await agents.remediate_llm(self.backend, diagnosis, problems, self.workspace, feedback)
            except Exception as exc:
                self._event(incident, "propose", "warning", f"Model call failed: {exc}")
        change = self._checked(incident, patch)
        if change is None and rule_diagnosis is not None:
            if model_diagnosed:
                self._event(incident, "propose", "warning", "Falling back to the rule engine's patch.")
                self._stage(incident, "propose", agents.RULES_AUTHOR)
            change = self._checked(incident, agents.remediate_rules(rule_diagnosis, self.workspace))
            if change is not None and diagnosis is not rule_diagnosis:
                diagnosis = rule_diagnosis
                incident["diagnosis"] = diagnosis.public()
                self._event(incident, "diagnosis", "result",
                            f"Root cause ({diagnosis.author}): {diagnosis.root_cause}")
        if change is None:
            self._event(incident, "propose", "warning", "No safe patch could be produced.")
            return None, diagnosis
        incident["patch"] = {"file": ", ".join(change.files), "author": change.author,
                             "explanation": change.explanation, "diff": change.diff(), "kind": change.kind}
        self._event(incident, "propose", "result", f"Patch for {', '.join(change.files)}: {change.explanation}")
        await self._pace()
        return change, diagnosis

    def _checked(self, incident: dict[str, Any], patch: agents.Patch | None) -> Change | None:
        if patch is None:
            return None
        try:
            after, _ = agents.safety_check(self.workspace, patch)
        except agents.PatchError as exc:
            self._event(incident, "propose", "warning", f"Safety check rejected the patch from {patch.author}: {exc}")
            return None
        self._event(incident, "propose", "tool", f"Safety check passed: dry-run of {patch.file} succeeded.")
        return Change("patch", patch.author, patch.explanation,
                      {patch.file: (self.workspace.read(patch.file), after)})

    async def _gate(self, incident: dict[str, Any], change: Change) -> bool:
        severity = incident["severity"]
        required = approval_mode_for_severity(severity) != "auto" or self.require_approval_all
        digest = change.digest(incident["id"])
        incident["patch"]["digest"] = digest
        if not required:
            incident["approval"] = {"required": False, "digest": digest,
                                    "decision": "automatic", "policy": f"{severity} runs automatically"}
            self._stage(incident, "approval", "policy")
            self._event(incident, "approval", "result", f"{severity}: approved automatically by policy.")
            await self._pace()
            return True
        reason = (f"{severity} requires a human to approve this exact change"
                  if approval_mode_for_severity(severity) != "auto" else "You asked to approve every fix")
        event = asyncio.Event()
        self._approval = {"incident_id": incident["id"], "digest": digest, "decision": None,
                          "expires_at": time.time() + self.options.approval_timeout, "event": event}
        incident["approval"] = {"required": True, "digest": digest, "decision": None, "policy": reason,
                                "expires_at": self._approval["expires_at"]}
        incident["status"] = "awaiting_approval"
        self._stage(incident, "approval", "you")
        self._event(incident, "approval", "thought", f"{reason}. Waiting for you (change sha256 {digest[:12]}).")
        with contextlib.suppress(asyncio.TimeoutError):
            await asyncio.wait_for(event.wait(), self.options.approval_timeout)
        decision = self._approval["decision"] or "timeout"
        self._approval = None
        incident["approval"]["decision"] = decision
        incident["status"] = "open"
        if decision != "approved":
            outcome = "you rejected the change" if decision == "rejected" else "the approval timed out"
            self._event(incident, "approval", "warning", f"Blocked: {outcome}. No change was made.")
            self.suppressed_version = self.workspace.version()
            self._close(incident, "escalated", f"Blocked because {outcome}")
            return False
        self._event(incident, "approval", "result", "You approved the change.")
        return True

    def _apply(self, incident: dict[str, Any], change: Change) -> None:
        self._stage(incident, "apply", change.author)
        incident["applied_at"] = time.time()
        for name, (before, after) in change.files.items():
            if self.workspace.read(name) != before:
                raise RuntimeError(f"{name} changed after the patch was checked; not applying it")
            self.workspace.write(name, after)
            self._event(incident, "apply", "tool", f"write_file {name}")

    def _revert(self, incident: dict[str, Any], change: Change) -> None:
        for name, (before, after) in change.files.items():
            if self.workspace.read(name) == after:
                self.workspace.write(name, before)
        self._event(incident, "verification", "warning", f"Reverted the failed change to {', '.join(change.files)}.")

    async def _verify(self, incident: dict[str, Any]) -> str:
        self._stage(incident, "verification", "independent server and browser checks")
        version = self.workspace.version()
        self._last_checks = await self.run_server_checks()
        self.server, self.server_version = self._last_checks, version
        bad = failing(self._last_checks)
        self._event(incident, "verification", "tool",
                    f"Server checks: {len(self._last_checks) - len(bad)}/{len(self._last_checks)} pass"
                    + (f" (failing: {', '.join(check.label for check in bad)})" if bad else ""))
        self._event(incident, "verification", "thought", "Waiting for the store page to reload and report.")
        report = await self._wait_for_browser(version, after=incident["applied_at"])
        if report is None:
            self._event(incident, "verification", "warning",
                        f"No browser report for this version within {self.options.verify_timeout:.0f}s.")
            return "no_browser" if not bad else "failed"
        browser = browser_checks(report, slow_api_ms=self.options.slow_api_ms)
        self._last_checks = self._last_checks + browser
        browser_bad = [check for check in browser if check.ok is not True]
        self._event(incident, "verification", "tool",
                    f"Browser checks: {len(browser) - len(browser_bad)}/{len(browser)} pass"
                    + (f" (failing: {', '.join(check.label for check in browser_bad)})" if browser_bad else ""))
        if all_pass(self._last_checks):
            self._event(incident, "verification", "result", "Verified: every server and browser check passes.")
            return "verified"
        self._event(incident, "verification", "warning", "Not resolved: checks still fail.")
        return "failed"

    async def _wait_for_browser(self, version: str, *, after: float) -> dict[str, Any] | None:
        deadline = time.monotonic() + self.options.verify_timeout
        while (
            version not in self.browser
            or self.browser[version]["received_at"] <= after
        ):
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self._report.clear()
            with contextlib.suppress(asyncio.TimeoutError):
                await asyncio.wait_for(self._report.wait(), remaining)
        return self.browser[version]

    async def _rollback(self, incident: dict[str, Any]) -> bool:
        changed = self.workspace.changed_files()
        self._stage(incident, "propose", "Rollback policy (not AI)")
        if not changed:
            self._close(incident, "escalated", "No agent fix was verified and nothing differs from the release")
            return False
        change = Change("rollback", "Rollback policy (not AI)",
                        f"Restore the last known-good release of {', '.join(changed)}.",
                        {name: (self.workspace.read(name), self.workspace.pristine(name)) for name in changed})
        self._event(incident, "rollback", "warning",
                    "No agent fix was verified. Falling back to the rollback policy, which is not AI.")
        incident["patch"] = {"file": ", ".join(changed), "author": change.author,
                             "explanation": change.explanation, "diff": change.diff(), "kind": "rollback"}
        if not await self._gate(incident, change):
            return False
        self._apply(incident, change)
        try:
            verdict = await self._verify(incident)
        except BaseException:
            self._revert(incident, change)
            raise
        incident["attempts"].append({"number": len(incident["attempts"]) + 1, "author": change.author,
                                     "file": ", ".join(changed), "verdict": verdict})
        if verdict != "verified":
            self.suppressed_version = self.workspace.version()
            self._close(incident, "escalated", "Even the rollback did not pass verification")
            return False
        incident["resolved_by"] = change.author
        return True

    async def _comms(self, incident: dict[str, Any]) -> None:
        self._stage(incident, "comms", "a fixed template (no model)")
        incident["status"] = "resolved"
        incident["resolve_s"] = round(time.time() - incident["opened_at"], 1)
        incident["summary"] = agents.comms_summary(incident)
        self._event(incident, "comms", "result", incident["summary"])
        self._close(incident, "resolved", "Resolved and verified")

    # --- bookkeeping ---------------------------------------------------------

    def _close(self, incident: dict[str, Any], status: str, outcome: str) -> None:
        if incident["closed_at"] is not None:
            return
        incident["status"], incident["outcome"] = status, outcome
        incident["closed_at"] = time.time()
        incident["stage"], incident["active_agent"], incident["active_backend"] = "closed", None, None
        if incident["summary"] is None:
            incident["summary"] = agents.comms_summary(incident)
        injected_files = {item["file"] for item in incident["injected"]}
        if injected_files and incident["diagnosis"]:
            incident["diagnosis_matched"] = incident["diagnosis"]["file"] in injected_files
        self.history.insert(0, {key: incident[key] for key in (
            "id", "status", "severity", "title", "detect_s", "resolve_s", "resolved_by",
            "diagnosis_matched", "outcome", "opened_at")} | {
            "injected": [item["name"] for item in incident["injected"]]})
        del self.history[self.options.history_limit:]
        if self.log_path:
            with self.log_path.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(incident, default=str) + "\n")

    def _stage(self, incident: dict[str, Any], stage: str, backend: str) -> None:
        incident["stage"], incident["active_agent"], incident["active_backend"] = stage, _AGENT[stage], backend

    def _event(self, incident: dict[str, Any], stage: str, kind: str, text: str, detail: str = "") -> None:
        incident["events"].append({"t": round(time.time() - incident["opened_at"], 1), "stage": stage,
                                   "agent": _AGENT[stage], "kind": kind, "text": text, "detail": detail})

    async def _pace(self, factor: float = 1.0) -> None:
        if self.options.step_delay:
            await asyncio.sleep(self.options.step_delay * factor)

    # --- view model -----------------------------------------------------------

    def snapshot(self) -> dict[str, Any]:
        version = self.workspace.version()
        incident = None
        if self.incident:
            incident = {key: value for key, value in self.incident.items()}
            if incident.get("approval") and incident["approval"].get("expires_at"):
                incident["approval"] = dict(incident["approval"],
                                            expires_in=max(0, round(incident["approval"]["expires_at"] - time.time())))
        report = self.latest_browser
        return {
            "backend": self.backend.public(),
            "settings": {"require_approval_all": self.require_approval_all},
            "site": {"version": version, "changed_files": self.workspace.changed_files(),
                     "monitoring_paused": version == self.suppressed_version},
            "catalog": [bug.public() for bug in BUGS],
            "injected": self.injected,
            "active": self.active,
            "incident": incident,
            "history": self.history[:20],
            "stages": [{"key": key, "agent": agent} for key, agent in STAGES],
            "checks": {
                "server": [check.public() for check in self.server],
                "server_current": self.server_version == version,
                "browser": [check.public() for check in browser_checks(report, slow_api_ms=self.options.slow_api_ms)],
                "browser_current": bool(report) and report["version"] == version,
                "browser_age_s": round(time.time() - report["received_at"], 1) if report else None,
            },
        }
