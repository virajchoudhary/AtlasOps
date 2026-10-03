"""Capture one explicitly governed Validation episode without injecting a fault."""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from bench.integrated_inference import (
    ARMS,
    EFFECTIVE_GENERATION_CONFIG,
    DirectActionCompletionPolicy,
    PairedCompletionEngine,
)

CONTEXT = "kind-atlasops-local"
SAFE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z")


def _validation_ids() -> list[str]:
    from config.splits import get_split

    return list(get_split("val"))


def _write_new(path: Path, value: dict[str, Any]) -> None:
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(value, stream, sort_keys=True, indent=2, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())


async def capture_validation_episode(
    *,
    engine: PairedCompletionEngine,
    arm: str,
    scenario_id: str,
    alert: dict[str, Any],
    run_id: str,
    incident_id: str,
    output_dir: Path,
    split_name: str = "val",
    execute_actions: bool = False,
    kube_context: str | None = None,
    episode_timeout_seconds: float = 3600,
) -> dict[str, Any]:
    """Use one arm for all generations; preserve an episode before returning."""
    if split_name != "val":
        raise ValueError("Integrated Base/SFT episodes are Validation-only")
    if arm not in ARMS:
        raise ValueError("Episode arm must be base or sft")
    if scenario_id not in _validation_ids():
        raise ValueError("Scenario is not in the frozen Validation population")
    if not isinstance(engine, PairedCompletionEngine):
        raise TypeError("Episode requires the shared paired inference engine")
    if any(not isinstance(value, str) or not SAFE_ID.fullmatch(value) for value in (run_id, incident_id)):
        raise ValueError("Run and incident IDs must be safe unique identifiers")
    if not incident_id.startswith("inc-"):
        raise ValueError("Incident ID must begin with inc-")
    if execute_actions is not True or kube_context != CONTEXT:
        raise PermissionError("Explicit action opt-in and pinned local Kind context are required")
    if (
        os.getenv("KUBECONFIG_CONTEXT") != CONTEXT
        or os.getenv("ATLASOPS_RL_POLICY_EXECUTE_ACTIONS") != "1"
    ):
        raise PermissionError("Coordinator policy opt-in/context must match the episode")
    if (
        isinstance(episode_timeout_seconds, bool)
        or not isinstance(episode_timeout_seconds, (int, float))
        or not math.isfinite(episode_timeout_seconds)
        or not 0 < episode_timeout_seconds <= 3600
    ):
        raise ValueError("Episode timeout must be finite, positive and at most 3600 seconds")
    if not isinstance(alert, dict):
        raise TypeError("Episode requires the observed operational alert")
    alert_copy = json.loads(json.dumps(alert, allow_nan=False))
    if engine.journal_path is None or engine._closed or engine._poisoned:
        raise ValueError("Episode requires an open, usable inference journal/engine")
    output = Path(output_dir).resolve()
    protected = (
        Path(__file__).resolve().parents[1],
        engine.checkpoint.resolve(),
        engine.base_snapshot.resolve(),
    )
    if any(output.is_relative_to(path) for path in protected):
        raise ValueError("Episode output must be outside source/model directories")
    if output.exists():
        raise FileExistsError("Episode output directory already exists")

    from agents import coordinator

    context = {"run_id": run_id, "incident_id": incident_id, "arm": arm, "scenario_id": scenario_id}
    async with engine._episode_lock:
        output.mkdir(parents=True, exist_ok=False)
        provider = engine.provider(arm, evidence_context=context)
        policy = DirectActionCompletionPolicy(provider)
        record: dict[str, Any] = {
            "schema_version": "atlasops-integrated-validation-episode-v1",
            **context,
            "split": "val",
            "scenario_id": scenario_id,
            "started_at_utc": datetime.now(UTC).isoformat(),
            "episode_status": "started",
            "alert": alert_copy,
            "inference_journal": str(engine.journal_path),
            "inference_timeout_seconds": engine.rpc_timeout_seconds,
            "model_load_timeout_seconds": engine.load_timeout_seconds,
            "execution_backend": "fixture_in_process" if engine.fixture_backend else "spawned_local_process",
            "effective_generation_config": dict(EFFECTIVE_GENERATION_CONFIG),
            "episode_timeout_seconds": episode_timeout_seconds,
            "evidence_class": "NON_EMPIRICAL",
            "certification_status": "NOT_CERTIFIED",
            "empirical_claim_allowed": False,
            "resolution": None,
            "reward": None,
            "time_to_resolve_s": None,
        }
        _write_new(output / "episode_started.json", record)
        started = time.monotonic()
        cancellation = None
        try:
            async with asyncio.timeout(episode_timeout_seconds):
                incident = await coordinator.handle_incident(
                    alert_copy,
                    incident_id=incident_id,
                    scenario_id=scenario_id,
                    completion_provider=provider,
                    remediation_policy=policy,
                    policy_seed=EFFECTIVE_GENERATION_CONFIG["seed"],
                    policy_generation_config={
                        key: EFFECTIVE_GENERATION_CONFIG[key]
                        for key in ("max_new_tokens", "temperature", "top_p")
                    },
                )
            record["episode_status"] = "captured_for_review"
            record["incident"] = incident
        except TimeoutError:
            record.update(episode_status="timeout", failure_category="episode_timeout")
        except asyncio.CancelledError as exc:
            record.update(episode_status="interrupted", failure_category="cancelled")
            cancellation = exc
        except Exception as exc:
            record.update(
                episode_status="failed",
                failure_category=getattr(exc, "failure_category", type(exc).__name__),
            )
        record["completed_at_utc"] = datetime.now(UTC).isoformat()
        record["elapsed_seconds"] = time.monotonic() - started
        record["within_episode_budget"] = record["elapsed_seconds"] <= episode_timeout_seconds
        record["deadline_enforcement"] = "cooperative_coordinator_timeout_with_post_return_budget_check"
        if record["episode_status"] == "captured_for_review" and not record["within_episode_budget"]:
            record.update(episode_status="timeout", failure_category="episode_deadline_overrun")
        if record["episode_status"] != "captured_for_review":
            partials = sorted(coordinator.TRAJECTORIES_DIR.glob(f"{incident_id}.partial-*.json"))
            record["partial_records"] = [
                {
                    "path": str(path),
                    "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
                    "size_bytes": path.stat().st_size,
                }
                for path in partials
            ]
        _write_new(output / "episode_finished.json", record)
        if cancellation is not None:
            raise cancellation
        return record
