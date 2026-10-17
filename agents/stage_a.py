"""Private causal controls for one prospective Stage A incident."""

from __future__ import annotations

import math
import re
import time
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any


STAGE_A_VERSION = "g4-stage-a-causal-v1"
ACTION_LIMIT_SECONDS = 480
RECOVERY_LIMIT_SECONDS = 540
FAULT_DURATION_SECONDS = 600
CPU_RECOVERY_INCREASE_CORES = 0.15
_ACTIVE_RUN: ContextVar[StageARun | None] = ContextVar("atlasops_stage_a", default=None)


def timestamp_seconds(value: Any) -> float:
    if isinstance(value, bool):
        raise ValueError("Invalid evidence timestamp")
    if isinstance(value, (int, float)):
        result = float(value)
    elif isinstance(value, str):
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("Evidence timestamp requires a timezone")
        result = parsed.timestamp()
    else:
        raise ValueError("Missing evidence timestamp")
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Invalid evidence timestamp")
    return result


def target_identity(payload: dict[str, Any]) -> dict[str, str]:
    items = payload.get("items")
    if not isinstance(items, list) or len(items) != 1:
        raise ValueError("Stage A requires exactly one eligible paymentservice pod")
    pod = items[0]
    metadata = pod.get("metadata", {})
    status = pod.get("status", {})
    spec = pod.get("spec", {})
    containers = spec.get("containers", [])
    statuses = status.get("containerStatuses", [])
    if (
        metadata.get("namespace") != "default"
        or metadata.get("labels", {}).get("app") != "paymentservice"
        or metadata.get("deletionTimestamp")
        or status.get("phase") != "Running"
        or len(containers) != 1
        or containers[0].get("name") != "server"
        or len(statuses) != 1
        or statuses[0].get("name") != "server"
        or statuses[0].get("ready") is not True
        or not isinstance(statuses[0].get("state", {}).get("running"), dict)
        or containers[0].get("resources", {}).get("limits", {}).get("cpu") != "200m"
    ):
        raise ValueError("Stage A target container is not the healthy pinned server")
    identity = {
        "namespace": metadata.get("namespace"),
        "pod": metadata.get("name"),
        "pod_uid": metadata.get("uid"),
        "container": "server",
        "container_id": statuses[0].get("containerID"),
        "cpu_limit": "200m",
    }
    if any(not isinstance(value, str) or not value.strip() for value in identity.values()):
        raise ValueError("Stage A target has incomplete immutable identity")
    if not re.fullmatch(r"[a-z0-9]([-a-z0-9]*[a-z0-9])?", identity["pod"]):
        raise ValueError("Stage A target pod name is invalid")
    return identity


def cpu_identity_matches(labels: dict[str, Any], target: dict[str, str]) -> bool:
    cgroup = labels.get("id")
    container_id = target["container_id"].split("://", 1)[-1]
    if not isinstance(cgroup, str) or not container_id:
        return False
    normalized = cgroup.replace("_", "-")
    pod_pattern = rf'(^|[/.-])pod{re.escape(target["pod_uid"])}([/.]|$)'
    return bool(
        labels.get("namespace") == target["namespace"]
        and labels.get("pod") == target["pod"]
        and labels.get("container") == target["container"]
        and re.search(pod_pattern, normalized)
        and (
            normalized.endswith(f"/cri-containerd-{container_id}.scope")
            or normalized.endswith(f"/{container_id}")
        )
    )


def bind_cpu_identity(observation: dict[str, Any], target: dict[str, str]) -> dict[str, Any]:
    observation["target_identity"] = dict(target)
    if not observation.get("series") or not all(
        cpu_identity_matches(sample.get("metric", {}), target)
        for sample in observation["series"]
    ):
        observation["fresh"] = False
        observation.setdefault("failures", []).append("CPU cgroup identity does not match the target")
    return observation


@dataclass
class StageARun:
    target: dict[str, str]
    baseline: dict[str, Any] = field(default_factory=dict)
    fault: dict[str, Any] | None = None
    apply_started_epoch: float | None = None
    apply_started_monotonic: float | None = None
    action_started_epoch: float | None = None
    action_completed_epoch: float | None = None
    recovery_observations: list[dict[str, Any]] = field(default_factory=list)
    created_uid: str | None = None
    cpu_recovery: dict[str, Any] | None = None

    def bind_created_resource(self, payload: dict[str, Any]) -> None:
        metadata = payload.get("metadata", {})
        if (
            payload.get("kind") != "StressChaos"
            or metadata.get("name") != "sf-002-paymentservice-cpu"
            or metadata.get("namespace") != "chaos-mesh"
            or not isinstance(metadata.get("uid"), str)
            or not metadata["uid"]
        ):
            raise ValueError("Stage A create response has no exact resource identity")
        self.created_uid = metadata["uid"]

    def begin_apply(self) -> None:
        if not self.baseline_valid_at(time.time()):
            raise RuntimeError("Stage A baseline is stale or not identity-bound at injection")
        self.apply_started_epoch = time.time()
        self.apply_started_monotonic = time.monotonic()

    def baseline_valid_at(self, epoch: float) -> bool:
        observation = self.baseline
        try:
            return bool(
                observation["fresh"] is True and observation["query_success"] is True
                and observation["target_identity"] == self.target
                and observation["series"]
                and all(
                    cpu_identity_matches(sample["metric"], self.target)
                    and -5 <= epoch - timestamp_seconds(sample["timestamp"]) <= 60
                    and -5 <= epoch - timestamp_seconds(sample["source_timestamp"]) <= 60
                    for sample in observation["series"]
                )
            )
        except (KeyError, TypeError, ValueError):
            return False

    def bind_fault(self, payload: dict[str, Any]) -> None:
        from agents.tools.chaos import _chaos_activity_state

        metadata = payload.get("metadata", {})
        spec = payload.get("spec", {})
        status = payload.get("status", {})
        experiment = status.get("experiment", {})
        conditions = status.get("conditions", [])
        records = experiment.get("containerRecords", [])
        if (
            payload.get("kind") != "StressChaos"
            or metadata.get("name") != "sf-002-paymentservice-cpu"
            or metadata.get("namespace") != "chaos-mesh"
            or not metadata.get("uid")
            or metadata.get("uid") != self.created_uid
            or not metadata.get("resourceVersion")
            or spec.get("duration") != "10m"
            or spec.get("mode") != "one"
            or spec.get("selector") != {
                "namespaces": ["default"], "labelSelectors": {"app": "paymentservice"},
            }
            or spec.get("stressors") != {"cpu": {"workers": 4, "load": 90}}
            or spec.get("stressngStressors")
            or spec.get("remoteCluster")
            or _chaos_activity_state(experiment.get("phase"), conditions, experiment) != "active"
            or not isinstance(records, list)
            or len(records) != 1
        ):
            raise ValueError("Stage A fault is not the exact active injected resource")
        record = records[0]
        record_id = f'{self.target["namespace"]}/{self.target["pod"]}/{self.target["container"]}'
        instance = status.get("instances", {}).get(record_id, {})
        if (
            record.get("phase", "").casefold() != "injected"
            or record.get("id") != record_id
            or record.get("selectorKey") != "."
            or not isinstance(instance.get("uid"), str)
            or not instance["uid"]
        ):
            raise ValueError("Stage A injected record does not match the bound target")
        # The API has pod UIDs and runtime container IDs, not separate container UIDs.
        created = timestamp_seconds(metadata.get("creationTimestamp"))
        if self.apply_started_epoch is None or self.apply_started_monotonic is None:
            raise ValueError("Stage A has no apply-start chronology")
        if created < self.apply_started_epoch - 5 or created > time.time() + 5:
            raise ValueError("Stage A resource creation time is inconsistent")
        injected = timestamp_seconds(instance.get("startTime"))
        if not created - 5 <= injected <= time.time() + 5:
            raise ValueError("Stage A stress instance has inconsistent injection chronology")
        self.fault = {
            "kind": "StressChaos",
            "name": metadata["name"],
            "namespace": metadata["namespace"],
            "uid": metadata["uid"],
            "resource_version": metadata["resourceVersion"],
            "created_at": created,
            "expires_at": min(created, self.apply_started_epoch) + FAULT_DURATION_SECONDS,
            "container_records": records,
            "stress_instance": instance,
            "target_identity": dict(self.target),
            "conditions": conditions,
            "observed_at": time.time(),
            "kube_context": "kind-atlasops-local",
        }

    def before_mutation(self, action: dict[str, Any], observation: dict[str, Any]) -> None:
        self.require_action_window()
        if self.action_completed_epoch is not None:
            raise RuntimeError("Stage A mutation budget already consumed")
        if action.get("tool") != "chaos_stop_experiment" or action.get("arguments") != {
            "kind": "StressChaos", "name": "sf-002-paymentservice-cpu",
            "namespace": "chaos-mesh",
        }:
            raise RuntimeError("Stage A permits only the exact fault-removal action")
        active = observation.get("active_experiments", [])
        matches = [
            item for item in active
            if item.get("uid") == self.created_uid
            and item.get("kind") == "StressChaos"
            and item.get("name") == "sf-002-paymentservice-cpu"
            and item.get("namespace") == "chaos-mesh"
        ]
        if len(matches) != 1:
            raise RuntimeError("Stage A active fault UID was not independently observed")
        if self.action_started_epoch is None:
            self.action_started_epoch = time.time()

    def require_action_window(self) -> None:
        if not self.fault or self.apply_started_monotonic is None or self.apply_started_epoch is None:
            raise RuntimeError("Stage A fault identity and chronology are required")
        elapsed = time.monotonic() - self.apply_started_monotonic
        epoch = time.time()
        if (
            not 0 <= elapsed <= ACTION_LIMIT_SECONDS
            or epoch > self.apply_started_epoch + ACTION_LIMIT_SECONDS
            or epoch >= self.fault["expires_at"]
        ):
            raise RuntimeError("Stage A action deadline expired")

    def require_recovery_window(self) -> None:
        if not self.fault or self.apply_started_monotonic is None or self.apply_started_epoch is None:
            raise RuntimeError("Stage A fault chronology is required")
        elapsed = time.monotonic() - self.apply_started_monotonic
        epoch = time.time()
        if (
            not 0 <= elapsed <= RECOVERY_LIMIT_SECONDS
            or epoch > self.apply_started_epoch + RECOVERY_LIMIT_SECONDS
            or epoch >= self.fault["expires_at"]
        ):
            raise RuntimeError("Stage A recovery deadline expired")

    def recovery_check(self, observation: dict[str, Any]) -> bool:
        self.recovery_observations.append(observation)
        self.require_recovery_window()
        baseline = self.baseline.get("max_cores")
        series = observation.get("series", [])
        if (
            self.action_completed_epoch is None
            or observation.get("target_identity") != self.target
            or observation.get("fresh") is not True
            or observation.get("query_success") is not True
            or not isinstance(baseline, (float, int))
            or isinstance(baseline, bool)
            or not math.isfinite(baseline)
            or not series
        ):
            return False
        samples: dict[float, float] = {}
        for recorded in self.recovery_observations:
            if recorded.get("fresh") is not True or recorded.get("query_success") is not True:
                continue
            for sample in recorded.get("series", []):
                labels = sample.get("metric", {})
                stamp = sample.get("source_timestamp")
                value = sample.get("value")
                if (
                    not cpu_identity_matches(labels, self.target)
                    or not isinstance(stamp, (int, float))
                    or not isinstance(value, (int, float))
                    or isinstance(stamp, bool)
                    or isinstance(value, bool)
                    or not math.isfinite(stamp)
                    or not math.isfinite(value)
                    or stamp <= self.action_completed_epoch
                    or stamp > time.time() + 5
                    or value < 0
                ):
                    continue
                if stamp in samples and samples[stamp] != value:
                    return False
                samples[stamp] = value
        ordered = sorted(samples.items())
        if len(ordered) < 2:
            return False
        first, last = ordered[-2:]
        if last[0] - first[0] < 30 or last[1] < first[1] or time.time() - last[0] > 60:
            return False
        cores = (last[1] - first[1]) / (last[0] - first[0])
        self.cpu_recovery = {
            "first_source_timestamp": first[0], "last_source_timestamp": last[0],
            "first_counter": first[1], "last_counter": last[1],
            "measured_cores": cores,
            "threshold_cores": baseline + CPU_RECOVERY_INCREASE_CORES,
            "passed": math.isfinite(cores) and cores < baseline + CPU_RECOVERY_INCREASE_CORES,
        }
        return self.cpu_recovery["passed"]

    def evidence(self) -> dict[str, Any]:
        return {
            "version": STAGE_A_VERSION,
            "target": self.target,
            "baseline": self.baseline,
            "fault": self.fault,
            "created_uid": self.created_uid,
            "apply_started_epoch": self.apply_started_epoch,
            "action_started_epoch": self.action_started_epoch,
            "action_completed_epoch": self.action_completed_epoch,
            "recovery_observations": self.recovery_observations,
            "cpu_recovery": self.cpu_recovery,
            "action_limit_seconds": ACTION_LIMIT_SECONDS,
            "recovery_limit_seconds": RECOVERY_LIMIT_SECONDS,
        }


def current_run() -> StageARun | None:
    return _ACTIVE_RUN.get()


def evaluate_acceptance(primary: dict[str, Any], cleanup: dict[str, Any]) -> dict[str, Any]:
    """Recompute the prospective acceptance from persisted records, not model claims."""
    criteria = {
        "causal_recovery": False,
        "pre_expiry_chronology": False,
        "post_action_cpu": False,
        "uid_bound_cleanup": False,
    }
    try:
        from scripts.run_stage4_golden_incident import evaluate_causal_g4_predicate

        recomputed = evaluate_causal_g4_predicate(
            **primary["causal_inputs"], incident_result=primary["incident_result"],
        )
        criteria["causal_recovery"] = bool(
            recomputed["gate_g4_pass"] is True
            and recomputed["criteria"] == primary["causal_criteria"]
            and primary["causal_recovery_pass"] is True
        )
        causal = primary["stage_a_causal"]
        fault = causal["fault"]
        target = causal["target"]
        start = timestamp_seconds(causal["apply_started_epoch"])
        action_start = timestamp_seconds(causal["action_started_epoch"])
        action_end = timestamp_seconds(causal["action_completed_epoch"])
        verified = timestamp_seconds(
            primary["phases"]["verification"]["verification_report"]["verification_timestamp"]
        )
        expires = min(start, timestamp_seconds(fault["created_at"])) + FAULT_DURATION_SECONDS
        criteria["pre_expiry_chronology"] = bool(
            causal["version"] == STAGE_A_VERSION
            and fault["uid"] == causal["created_uid"]
            and start <= action_start <= action_end <= start + ACTION_LIMIT_SECONDS
            and action_end <= verified <= start + RECOVERY_LIMIT_SECONDS
            and verified < expires
            and StageARun(target, baseline=causal["baseline"]).baseline_valid_at(start)
            and fault["target_identity"] == target
            and recomputed["executed_tool_calls"][0]["output"]["uid"]
            == causal["created_uid"]
        )
        baseline = causal["baseline"]["max_cores"]
        sample_values: dict[float, float] = {}
        for observation in causal["recovery_observations"]:
            if observation.get("fresh") is not True or observation.get("query_success") is not True:
                continue
            if observation.get("target_identity") != target:
                continue
            for sample in observation["series"]:
                labels = sample["metric"]
                stamp = timestamp_seconds(sample["source_timestamp"])
                value = float(sample["value"])
                if (
                    not cpu_identity_matches(labels, target)
                    or not action_end < stamp <= verified or verified - stamp > 60
                    or not math.isfinite(value) or value < 0
                ):
                    continue
                if stamp in sample_values and sample_values[stamp] != value:
                    raise ValueError("Conflicting counter samples")
                sample_values[stamp] = value
        ordered = sorted(sample_values.items())
        if len(ordered) >= 2 and isinstance(baseline, (int, float)) and not isinstance(baseline, bool):
            first, last = ordered[-2:]
            interval = last[0] - first[0]
            delta = last[1] - first[1]
            criteria["post_action_cpu"] = bool(
                math.isfinite(baseline) and baseline >= 0 and interval >= 30
                and delta >= 0 and delta / interval < baseline + CPU_RECOVERY_INCREASE_CORES
            )
        attempts = cleanup["attempts"]
        final = attempts[-1]
        observation = final["postflight_result"]
        criteria["uid_bound_cleanup"] = bool(
            cleanup["experiment_id"] == primary["experiment_id"]
            and cleanup["fault_uid"] == fault["uid"]
            and 1 <= len(attempts) <= cleanup["max_attempts"] <= 3
            and cleanup["verified_zero_chaos"] is True
            and observation["success"] is True
            and json_items_empty(observation["stdout"])
            and timestamp_seconds(attempts[0]["started_at"]) >= timestamp_seconds(primary["completed_at"])
        )
    except (KeyError, TypeError, ValueError, IndexError):
        pass
    return {"gate_g4_pass": all(criteria.values()), "criteria": criteria}


def json_items_empty(value: str) -> bool:
    import json

    payload = json.loads(value)
    return isinstance(payload, dict) and payload.get("items") == []


async def wait_for_cpu_recovery() -> bool:
    import asyncio

    from agents.verifier import verify_environment

    run = current_run()
    if run is None:
        return True
    deadline = time.monotonic() + 60
    while time.monotonic() < deadline:
        try:
            run.require_recovery_window()
        except RuntimeError:
            return False
        result = await asyncio.to_thread(
            verify_environment, "single_fault/sf-002", False,
        )
        if run.cpu_recovery and run.cpu_recovery["passed"]:
            try:
                run.require_recovery_window()
            except RuntimeError:
                return False
            return result.env_resolved is True
        remaining = deadline - time.monotonic()
        if remaining > 0:
            await asyncio.sleep(min(2, remaining))
    return False


@contextmanager
def stage_a_scope(run: StageARun):
    token = _ACTIVE_RUN.set(run)
    try:
        yield run
    finally:
        _ACTIVE_RUN.reset(token)
