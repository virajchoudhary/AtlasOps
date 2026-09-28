"""Prospective, non-executing adversarial Chaos proposal generator."""

import hashlib
import json
import math
import os
import re
import stat
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import yaml


REPO_ROOT = Path(__file__).resolve().parents[1]
JUDGE_URL = os.getenv("JUDGE_URL", "http://localhost:8001/v1")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "Qwen/Qwen2.5-72B-Instruct")
SCENARIO_ID = re.compile(r"^adv-[a-z0-9](?:[a-z0-9-]{0,43}[a-z0-9])?$")
MILLISECONDS = re.compile(r"^([1-9][0-9]{0,3})ms$")
MEGABYTES = re.compile(r"^([1-9][0-9]{0,2})MB$")
SECONDS_OFFSET = re.compile(r"^([+-])([1-9][0-9]{0,2})s$")
DURATION = re.compile(r"^([1-9]|1[0-5])m$")
ALLOWED_ACTIONS = {
    "PodChaos": {"pod-kill", "pod-failure", "container-kill"},
    "NetworkChaos": {"delay", "loss", "corrupt", "duplicate", "partition"},
    "StressChaos": {"cpu", "memory"},
    "DNSChaos": {"error", "random"},
    "IOChaos": {"fault", "latency"},
    "TimeChaos": {"offset"},
}

# All Chaos Mesh fault primitives the designer can combine
AVAILABLE_PRIMITIVES = [
    f"{kind}({action})"
    for kind, actions in ALLOWED_ACTIONS.items()
    for action in sorted(actions)
]

SERVICES = [
    "frontend", "cartservice", "checkoutservice", "paymentservice",
    "currencyservice", "shippingservice", "emailservice",
    "recommendationservice", "productcatalogservice", "adservice",
    "redis-cart",
]

DESIGNER_SYSTEM_PROMPT = """You are an elite chaos engineering adversary.
Your job is to design Kubernetes chaos experiments that will expose weaknesses
in an AI SRE agent's incident response capabilities.

You will be given:
1. The agent's recent failure history (which scenarios it struggled with)
2. The available Chaos Mesh fault primitives
3. The available target services

You must output a JSON object describing a NEW chaos scenario that:
- Targets the agent's SPECIFIC weaknesses (not generic failures)
- Combines 2-4 fault primitives simultaneously for realism
- Includes at least one red herring (a fault that LOOKS related but isn't the root cause)
- Has a clear root cause chain that requires multi-step reasoning to find

Output ONLY this JSON (no markdown, no explanation):
{
  "scenario_id": "adv-<unique-slug>",
  "title": "<one-line description>",
  "difficulty": "hard|expert|extreme",
  "root_cause_chain": ["service_A → symptom", "service_B → cascade"],
  "red_herrings": ["service_C looks broken but isn't the cause"],
  "weakness_targeted": "<which agent weakness this exploits>",
  "faults": [
    {
      "kind": "PodChaos|NetworkChaos|StressChaos|DNSChaos|IOChaos|TimeChaos",
      "action": "<specific action>",
      "target_service": "<service name>",
      "params": {"duration": "10m"}
    }
  ]
}
Use 2-4 faults, a nonempty root-cause chain and red-herring list, and only
the supplied primitives and services. For NetworkChaos(partition), include
params.peer_service from the supplied service list. Output is a proposal,
never an instruction to execute it."""


async def _call_judge(prompt: str) -> str:
    api_key = os.getenv("LLM_API_KEY", "")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    async with httpx.AsyncClient(
        timeout=60, headers=headers, trust_env=False
    ) as client:
        async with client.stream(
            "POST",
            f"{JUDGE_URL}/chat/completions",
            json={
                "model": JUDGE_MODEL,
                "messages": [
                    {"role": "system", "content": DESIGNER_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "temperature": 0.9,  # high temp = maximum creativity
            },
        ) as response:
            response.raise_for_status()
            body = bytearray()
            async for chunk in response.aiter_bytes():
                if len(body) + len(chunk) > 256 * 1024:
                    raise ValueError("Adversarial judge HTTP response exceeds 256 KiB")
                body.extend(chunk)
    try:
        content = json.loads(body)["choices"][0]["message"]["content"]
    except (ValueError, TypeError, KeyError, IndexError) as exc:
        raise ValueError("Invalid adversarial judge HTTP response") from exc
    if not isinstance(content, str):
        raise ValueError("Invalid adversarial judge HTTP content")
    return content


def _extract_weaknesses(failure_history: list[dict]) -> list[str]:
    """Summarise what the agent repeatedly fails at."""
    weakness_counts: dict[str, int] = {}
    for episode in failure_history:
        if episode.get("resolved"):
            continue
        tier    = episode.get("tier", "unknown")
        sc_id   = episode.get("scenario_id", "")
        judge   = episode.get("judge", {}) or {}
        if judge.get("reasoning", 1.0) < 0.4:
            weakness_counts["poor evidence-gathering"] = weakness_counts.get("poor evidence-gathering", 0) + 1
        if judge.get("efficiency", 1.0) < 0.4:
            weakness_counts["unsafe remediation shortcuts"] = weakness_counts.get("unsafe remediation shortcuts", 0) + 1
        if "dns" in sc_id.lower():
            weakness_counts["DNS failure diagnosis"] = weakness_counts.get("DNS failure diagnosis", 0) + 1
        if "cascade" in tier:
            weakness_counts["cascade root cause tracing"] = weakness_counts.get("cascade root cause tracing", 0) + 1
        if "network" in sc_id.lower():
            weakness_counts["network partition handling"] = weakness_counts.get("network partition handling", 0) + 1
        if "time" in sc_id.lower():
            weakness_counts["clock skew detection"] = weakness_counts.get("clock skew detection", 0) + 1
    # Return top 3 weaknesses
    return sorted(weakness_counts, key=weakness_counts.get, reverse=True)[:3]


def _valid_unit_score(value: Any) -> bool:
    return (
        type(value) in (int, float)
        and (type(value) is int or math.isfinite(value))
        and 0 <= value <= 1
    )


def _text(value: Any, label: str, *, limit: int = 240) -> str:
    if (
        not isinstance(value, str)
        or not value.strip()
        or len(value) > limit
        or not value.isprintable()
    ):
        raise ValueError(f"Invalid adversarial {label}")
    return value.strip()


def _integer(value: Any, label: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"Invalid adversarial {label}")
    return value


def _milliseconds(value: Any, label: str) -> str:
    match = MILLISECONDS.fullmatch(value) if isinstance(value, str) else None
    if match is None or int(match.group(1)) > 5000:
        raise ValueError(f"Invalid adversarial {label}")
    return value


def _percent(value: Any, label: str) -> str:
    if isinstance(value, str) and value.isascii() and value.isdecimal():
        if len(value) > 3:
            raise ValueError(f"Invalid adversarial {label}")
        value = int(value)
    return str(_integer(value, label, 1, 100))


def _validate_fault(fault: Any) -> dict[str, Any]:
    if not isinstance(fault, dict) or set(fault) != {
        "kind", "action", "target_service", "params",
    }:
        raise ValueError("Invalid adversarial fault fields")
    kind, action, service = (
        fault["kind"], fault["action"], fault["target_service"]
    )
    if (
        not isinstance(kind, str)
        or not isinstance(action, str)
        or kind not in ALLOWED_ACTIONS
        or action not in ALLOWED_ACTIONS[kind]
    ):
        raise ValueError("Invalid adversarial fault action")
    if not isinstance(service, str) or service not in SERVICES:
        raise ValueError("Invalid adversarial target service")
    params = fault["params"]
    if not isinstance(params, dict):
        raise ValueError("Invalid adversarial fault params")
    allowed = {
        ("PodChaos", "container-kill"): {"container_name"},
        ("NetworkChaos", "delay"): {"latency"},
        ("NetworkChaos", "loss"): {"loss"},
        ("NetworkChaos", "corrupt"): {"corrupt"},
        ("NetworkChaos", "duplicate"): {"duplicate"},
        ("NetworkChaos", "partition"): {"peer_service"},
        ("StressChaos", "cpu"): {"workers", "load"},
        ("StressChaos", "memory"): {"workers", "size"},
        ("IOChaos", "fault"): {"percent"},
        ("IOChaos", "latency"): {"percent", "delay"},
        ("TimeChaos", "offset"): {"offset"},
    }.get((kind, action), set()) | {"duration"}
    if set(params) - allowed:
        raise ValueError("Invalid adversarial fault params")
    duration = params.get("duration", "10m")
    if not isinstance(duration, str) or DURATION.fullmatch(duration) is None:
        raise ValueError("Invalid adversarial duration")
    normalized: dict[str, Any] = {"duration": duration}
    if kind == "PodChaos" and action == "container-kill":
        container = params.get("container_name", service)
        if container != service:
            raise ValueError("Invalid adversarial container name")
        normalized["container_name"] = container
    elif kind == "NetworkChaos":
        if action == "delay":
            normalized["latency"] = _milliseconds(
                params.get("latency", "1000ms"), "latency"
            )
        elif action in {"loss", "corrupt", "duplicate"}:
            normalized[action] = _percent(params.get(action, 30), action)
        elif action == "partition":
            peer = params.get("peer_service")
            if peer not in SERVICES or peer == service:
                raise ValueError("Invalid adversarial peer service")
            normalized["peer_service"] = peer
    elif kind == "StressChaos":
        normalized["workers"] = _integer(
            params.get("workers", 2), "workers", 1, 4
        )
        if action == "cpu":
            normalized["load"] = _integer(params.get("load", 80), "load", 1, 100)
        else:
            size = params.get("size", "256MB")
            match = MEGABYTES.fullmatch(size) if isinstance(size, str) else None
            if match is None or int(match.group(1)) > 512:
                raise ValueError("Invalid adversarial memory size")
            normalized["size"] = size
    elif kind == "IOChaos":
        normalized["percent"] = _integer(
            params.get("percent", 100), "IO percent", 1, 100
        )
        if action == "latency":
            normalized["delay"] = _milliseconds(
                params.get("delay", "100ms"), "IO delay"
            )
    elif kind == "TimeChaos":
        offset = params.get("offset", "+300s")
        match = SECONDS_OFFSET.fullmatch(offset) if isinstance(offset, str) else None
        if match is None or int(match.group(2)) > 600:
            raise ValueError("Invalid adversarial time offset")
        normalized["offset"] = offset
    return {
        "kind": kind, "action": action,
        "target_service": service, "params": normalized,
    }


def _validate_spec(value: Any) -> dict[str, Any]:
    required = {
        "scenario_id", "title", "difficulty", "root_cause_chain",
        "red_herrings", "weakness_targeted", "faults",
    }
    if not isinstance(value, dict) or set(value) != required:
        raise ValueError("Invalid adversarial judge response fields")
    scenario_id = value["scenario_id"]
    if not isinstance(scenario_id, str) or SCENARIO_ID.fullmatch(scenario_id) is None:
        raise ValueError("Invalid adversarial scenario ID")
    if (
        not isinstance(value["difficulty"], str)
        or value["difficulty"] not in {"hard", "expert", "extreme"}
    ):
        raise ValueError("Invalid adversarial difficulty")
    chain = value["root_cause_chain"]
    decoys = value["red_herrings"]
    faults = value["faults"]
    if not isinstance(chain, list) or not 2 <= len(chain) <= 8:
        raise ValueError("Invalid adversarial root cause chain")
    if not isinstance(decoys, list) or not 1 <= len(decoys) <= 4:
        raise ValueError("Invalid adversarial red herrings")
    if not isinstance(faults, list) or not 2 <= len(faults) <= 4:
        raise ValueError("Invalid adversarial fault count")
    return {
        "scenario_id": scenario_id,
        "title": _text(value["title"], "title", limit=160),
        "difficulty": value["difficulty"],
        "root_cause_chain": [_text(item, "root cause") for item in chain],
        "red_herrings": [_text(item, "red herring") for item in decoys],
        "weakness_targeted": _text(value["weakness_targeted"], "weakness", limit=160),
        "faults": [_validate_fault(fault) for fault in faults],
    }


def _fault_to_document(fault: dict[str, Any], scenario_id: str, index: int) -> dict:
    kind, action, service, params = (
        fault["kind"], fault["action"], fault["target_service"], fault["params"]
    )
    spec: dict[str, Any] = {
        "mode": "one",
        "selector": {
            "namespaces": ["default"],
            "labelSelectors": {"app": service},
        },
        "duration": params["duration"],
    }
    if kind == "PodChaos":
        spec["action"] = action
        if action == "container-kill":
            spec["containerNames"] = [params["container_name"]]
    elif kind == "NetworkChaos":
        spec["action"] = action
        if action == "delay":
            spec["delay"] = {"latency": params["latency"], "correlation": "25"}
        elif action in {"loss", "corrupt", "duplicate"}:
            spec[action] = {action: params[action], "correlation": "25"}
        else:
            spec["direction"] = "both"
            spec["target"] = {
                "mode": "one",
                "selector": {
                    "namespaces": ["default"],
                    "labelSelectors": {"app": params["peer_service"]},
                },
            }
    elif kind == "StressChaos":
        stressor = {"workers": params["workers"]}
        stressor["load" if action == "cpu" else "size"] = params[
            "load" if action == "cpu" else "size"
        ]
        spec["stressors"] = {action: stressor}
    elif kind == "DNSChaos":
        spec["action"] = action
        spec["patterns"] = [f"{service}.default.svc.cluster.local."]
    elif kind == "IOChaos":
        spec.update({
            "action": action,
            "volumePath": "/var/log",
            "path": "/var/log/**",
            "percent": params["percent"],
        })
        if action == "fault":
            spec["errno"] = 28
        else:
            spec["delay"] = params["delay"]
    elif kind == "TimeChaos":
        spec["timeOffset"] = params["offset"]
    return {
        "apiVersion": "chaos-mesh.org/v1alpha1",
        "kind": kind,
        "metadata": {
            "name": f"{scenario_id}-fault-{index}",
            "namespace": "chaos-mesh",
            "labels": {
                "scenario": scenario_id,
                "tier": "adversarial",
                "generated": "true",
            },
        },
        "spec": spec,
    }


def _fault_to_yaml(fault: dict, scenario_id: str, index: int) -> str:
    if not isinstance(scenario_id, str) or SCENARIO_ID.fullmatch(scenario_id) is None:
        raise ValueError("Invalid adversarial scenario ID")
    if type(index) is not int or not 0 <= index <= 3:
        raise ValueError("Invalid adversarial fault index")
    return yaml.safe_dump(
        _fault_to_document(_validate_fault(fault), scenario_id, index),
        sort_keys=False,
    )


def _validate_output_dir(output_dir: Path) -> Path:
    path = Path(output_dir).expanduser()
    if not path.is_absolute() or not path.is_dir():
        raise ValueError("Adversarial output must be an existing absolute directory")
    for component in (*reversed(path.parents), path):
        metadata = component.lstat()
        reparse = getattr(metadata, "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400
        )
        if stat.S_ISLNK(metadata.st_mode) or reparse or (
            hasattr(component, "is_junction") and component.is_junction()
        ):
            raise ValueError("Adversarial output cannot traverse a link or junction")
    destination = path.resolve(strict=True)
    root = REPO_ROOT.resolve()
    if destination == root or root in destination.parents:
        raise ValueError("Adversarial output must be outside the checkout")
    return destination


async def design_scenario(
    failure_history: list[dict], *, output_dir: Path,
) -> dict[str, Any]:
    """Save an unapproved proposal only after strict validation."""
    destination = _validate_output_dir(output_dir)
    if not isinstance(failure_history, list) or len(failure_history) > 100:
        raise ValueError("Adversarial failure history must be a bounded list")
    failed_ids = []
    for episode in failure_history:
        if not isinstance(episode, dict):
            raise ValueError("Invalid adversarial failure history")
        if type(episode.get("resolved")) is not bool:
            raise ValueError("Invalid adversarial failure history resolution")
        tier = episode.get("tier", "unknown")
        if not isinstance(tier, str) or re.fullmatch(
            r"[a-z_]{1,40}", tier
        ) is None:
            raise ValueError("Invalid adversarial failure history tier")
        judge = episode.get("judge", {})
        if not isinstance(judge, dict) or any(
            not _valid_unit_score(judge.get(key, 1.0))
            for key in ("reasoning", "efficiency")
        ):
            raise ValueError("Invalid adversarial failure history judge")
        scenario = episode.get("scenario_id", "")
        if not isinstance(scenario, str) or re.fullmatch(
            r"[a-z0-9_/-]{1,80}", scenario
        ) is None:
            raise ValueError("Invalid adversarial failure history scenario ID")
        if not episode.get("resolved"):
            failed_ids.append(scenario)
    weaknesses = _extract_weaknesses(failure_history)
    prompt = f"""Agent failure history summary:
- Failed scenarios: {failed_ids}
- Top weaknesses identified: {weaknesses}
- Total episodes analysed: {len(failure_history)}

Available fault primitives: {AVAILABLE_PRIMITIVES}
Available target services: {SERVICES}

Design a NEW adversarial chaos scenario that specifically targets: {weaknesses[0] if weaknesses else 'general multi-fault handling'}

Make it {('extreme' if len(failure_history) > 20 else 'expert' if len(failure_history) > 10 else 'hard')} difficulty."""

    raw = await _call_judge(prompt)
    if not isinstance(raw, str) or len(raw.encode("utf-8")) > 65536:
        raise ValueError("Invalid adversarial judge response size")
    try:
        parsed = json.loads(
            raw,
            parse_constant=lambda _value: (_ for _ in ()).throw(ValueError()),
        )
    except (json.JSONDecodeError, ValueError, TypeError) as exc:
        raise ValueError("Invalid adversarial judge response") from exc
    spec = _validate_spec(parsed)
    scenario_id = spec["scenario_id"]
    documents = [
        _fault_to_document(fault, scenario_id, index)
        for index, fault in enumerate(spec["faults"])
    ]
    manifest = yaml.safe_dump_all(documents, sort_keys=False, explicit_start=True)
    manifest_sha256 = hashlib.sha256(manifest.encode("utf-8")).hexdigest()
    metadata = {
        **spec,
        "generated_at": datetime.now(UTC).isoformat(),
        "weaknesses_targeted": weaknesses,
        "judge_model_requested": JUDGE_MODEL,
        "model_identity_status": "unverified_alias",
        "judge_response_sha256": hashlib.sha256(raw.encode("utf-8")).hexdigest(),
        "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
        "manifest_sha256": manifest_sha256,
        "evidence_classification": "GENERATED_UNAPPROVED",
        "empirical_claim_allowed": False,
        "execution_authorized": False,
    }
    manifest_path = destination / f"{scenario_id}.yaml"
    meta_path = destination / f"{scenario_id}.json"
    if any(path.exists() or path.is_symlink() for path in (manifest_path, meta_path)):
        raise FileExistsError("Adversarial scenario ID already exists")
    suffix = uuid.uuid4().hex
    temp_manifest = destination / f".{scenario_id}.{suffix}.yaml.tmp"
    temp_meta = destination / f".{scenario_id}.{suffix}.json.tmp"
    try:
        with temp_manifest.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(manifest)
        with temp_meta.open("x", encoding="utf-8", newline="\n") as stream:
            json.dump(metadata, stream, indent=2)
        os.link(temp_meta, meta_path)
        os.link(temp_manifest, manifest_path)
    finally:
        temp_manifest.unlink(missing_ok=True)
        temp_meta.unlink(missing_ok=True)
    return {
        "scenario_id": scenario_id,
        "manifest_path": str(manifest_path),
        "metadata_path": str(meta_path),
        "spec": metadata,
    }


async def design_batch(
    failure_history: list[dict], count: int = 10, *, output_dir: Path,
) -> list[dict]:
    """Generate at most ten unapproved proposals in an explicit directory."""
    if type(count) is not int or not 1 <= count <= 10:
        raise ValueError("Adversarial batch count must be from 1 to 10")
    destination = _validate_output_dir(output_dir)
    results = []
    for _ in range(count):
        results.append(await design_scenario(failure_history, output_dir=destination))
    return results
