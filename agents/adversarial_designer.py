"""Adversarial Scenario Designer.

Qwen2.5-72B judge analyses the agent's failure history and generates
brand-new Chaos Mesh YAML manifests targeting specific weaknesses.

Every run produces unique, never-seen-before scenarios — the benchmark
gets harder as the model improves, making the test set impossible to memorise.
"""

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
import yaml


JUDGE_URL   = os.getenv("JUDGE_URL",   "http://localhost:8001/v1")
JUDGE_MODEL = os.getenv("JUDGE_MODEL", "Qwen/Qwen2.5-72B-Instruct")
API_KEY     = os.getenv("LLM_API_KEY", "")

ADVERSARIAL_DIR = Path("bench/chaos_manifests/adversarial")
ADVERSARIAL_DIR.mkdir(parents=True, exist_ok=True)
_SAFE_SCENARIO_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")

# All Chaos Mesh fault primitives the designer can combine
AVAILABLE_PRIMITIVES = [
    "PodChaos(pod-kill)",
    "PodChaos(pod-failure)",
    "NetworkChaos(delay)",
    "NetworkChaos(loss)",
    "NetworkChaos(corrupt)",
    "NetworkChaos(duplicate)",
    "NetworkChaos(partition)",
    "StressChaos(cpu)",
    "StressChaos(memory)",
    "DNSChaos(error)",
    "DNSChaos(random)",
    "TimeChaos(offset)",
]

_SUPPORTED_FAULTS = {
    "PodChaos": {"pod-kill", "pod-failure"},
    "NetworkChaos": {"delay", "loss", "corrupt", "duplicate", "partition"},
    "StressChaos": {"cpu", "memory"},
    "DNSChaos": {"error", "random"},
    "TimeChaos": {"offset"},
}
_DURATION_RE = re.compile(r"^[+-]?[0-9]{1,5}(ms|s|m|h)$")
_PERCENT_RE = re.compile(r"^[0-9]{1,3}$")
_MEMORY_RE = re.compile(r"^[0-9]{1,4}(Ki|Mi|Gi)$")

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
      "params": {"key": "value"}
    }
  ]
}"""


async def _call_judge(prompt: str) -> str:
    headers = {"Authorization": f"Bearer {API_KEY}"} if API_KEY else {}
    async with httpx.AsyncClient(timeout=60, headers=headers) as client:
        r = await client.post(
            f"{JUDGE_URL}/chat/completions",
            json={
                "model": JUDGE_MODEL,
                "messages": [
                    {"role": "system", "content": DESIGNER_SYSTEM_PROMPT},
                    {"role": "user",   "content": prompt},
                ],
                "temperature": 0.9,  # high temp = maximum creativity
            },
        )
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"]


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


def _duration_seconds(value: object, *, signed: bool = False) -> float:
    text = str(value)
    if not _DURATION_RE.fullmatch(text):
        raise ValueError(f"invalid chaos duration: {value!r}")
    if not signed and text.startswith("-"):
        raise ValueError(f"chaos duration must not be negative: {value!r}")
    sign = -1 if text.startswith("-") else 1
    number = int(text.lstrip("+-")[:-2] if text.endswith("ms") else text.lstrip("+-")[:-1])
    unit = text[-2:] if text.endswith("ms") else text[-1:]
    seconds = number / 1000 if unit == "ms" else {
        "s": 1,
        "m": 60,
        "h": 3600,
    }[unit]
    return sign * seconds


def _bounded_duration(value: object, default: str = "15m") -> str:
    seconds = _duration_seconds(value if value is not None else default)
    if not 60 <= seconds <= 1800:
        raise ValueError(f"chaos duration must be 1m-30m: {value!r}")
    return str(value or default)


def _percent(value: object, default: int) -> int:
    if value is None:
        return default
    number = int(value if value is not None else default)
    if not isinstance(value, (int, float)) and not _PERCENT_RE.fullmatch(str(value)):
        raise ValueError(f"invalid percent: {value!r}")
    if not 0 <= number <= 100:
        raise ValueError(f"percent outside 0..100: {value!r}")
    return number


def _bounded_int(value: object, default: int, low: int, high: int) -> int:
    number = int(value if value is not None else default)
    if not low <= number <= high:
        raise ValueError(f"integer outside {low}..{high}: {value!r}")
    return number


def _bounded_network_delay(value: object, field: str) -> str:
    text = str(value)
    seconds = _duration_seconds(text)
    if not 0 <= seconds <= 60:
        raise ValueError(f"network {field} must be within 0-60 seconds: {value!r}")
    return text


def _bounded_memory_size(value: object) -> str:
    text = str(value)
    match = _MEMORY_RE.fullmatch(text)
    if not match:
        raise ValueError(f"invalid memory size: {value!r}")
    multipliers = {"Ki": 1 / 1024, "Mi": 1, "Gi": 1024}
    mebibytes = int(match.group(1)) * multipliers[match.group(2)]
    if not 0 < mebibytes <= 4096:
        raise ValueError(f"memory size must be within 1Ki-4Gi: {value!r}")
    return text


_ACTION_KINDS = frozenset({"PodChaos", "NetworkChaos", "DNSChaos"})


def _fault_document(fault: dict, scenario_id: str, index: int) -> dict:
    if not isinstance(fault, dict):
        raise ValueError("adversarial fault must be an object")
    kind = fault.get("kind")
    action = fault.get("action")
    service = fault.get("target_service")
    raw_params = fault.get("params", {})
    if kind not in _SUPPORTED_FAULTS:
        raise ValueError(f"unsupported adversarial fault kind: {kind!r}")
    if action not in _SUPPORTED_FAULTS[kind]:
        raise ValueError(f"unsupported adversarial fault action: {action!r}")
    if service not in SERVICES:
        raise ValueError(f"unsupported adversarial target service: {service!r}")
    if not isinstance(raw_params, dict):
        raise ValueError("adversarial fault params must be an object")
    params = dict(raw_params)
    allowed_params = {
        "PodChaos": {"duration"},
        "NetworkChaos": {"duration", "latency", "jitter", "correlation"},
        "StressChaos": {"duration", "workers", "load", "size"},
        "DNSChaos": {"duration"},
        "TimeChaos": {"duration", "offset"},
    }[kind]
    unexpected_common = set(params) - allowed_params
    if unexpected_common:
        raise ValueError(f"unsupported fault parameters: {sorted(unexpected_common)}")
    duration = _bounded_duration(params.pop("duration", None))

    document = {
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
        "spec": {
            "mode": "all",
            "selector": {
                "namespaces": ["default"],
                "labelSelectors": {"app": service},
            },
            "duration": duration,
        },
    }
    if kind in _ACTION_KINDS:
        document["spec"]["action"] = action

    if kind == "NetworkChaos":
        if action == "delay":
            unexpected = set(params) - {"latency", "jitter", "correlation"}
            if unexpected:
                raise ValueError(f"unsupported delay parameters: {sorted(unexpected)}")
            latency = _bounded_network_delay(params.get("latency", "1000ms"), "delay")
            spec_values = {
                "delay": {
                    "latency": latency,
                    "correlation": _percent(params.get("correlation"), 80),
                    **({"jitter": jitter} if (jitter := params.get("jitter")) else {}),
                }
            }
            if "jitter" in spec_values["delay"]:
                spec_values["delay"]["jitter"] = _bounded_network_delay(
                    spec_values["delay"]["jitter"], "jitter"
                )
            document["spec"].update(spec_values)
        else:
            if action == "partition":
                if params:
                    raise ValueError("NetworkChaos partition accepts no parameters")
                document["spec"].update({
                    "direction": "both",
                    "target": {
                        "mode": "all",
                        "selector": {"namespaces": ["default"]},
                    },
                })
            else:
                unexpected = set(params) - {action, "correlation"}
                if unexpected:
                    raise ValueError(f"unsupported network parameters: {sorted(unexpected)}")
                document["spec"][action] = {
                    action: str(_percent(params.get(action), 30)),
                    "correlation": str(_percent(params.get("correlation"), 25)),
                }
    elif kind == "StressChaos":
        unexpected = set(params) - (
            {"workers", "load"} if action == "cpu" else {"workers", "size"}
        )
        if unexpected:
            raise ValueError(f"unsupported stress parameters: {sorted(unexpected)}")
        stressor = (
            {"workers": _bounded_int(params.get("workers"), 4, 1, 16),
             "load": _bounded_int(params.get("load"), 80, 1, 100)}
            if action == "cpu"
            else {
                "workers": _bounded_int(params.get("workers"), 2, 1, 16),
                "size": _bounded_memory_size(params.get("size") or "256Mi"),
            }
        )
        document["spec"]["stressors"] = {action: stressor}
    elif kind == "DNSChaos":
        document["spec"]["patterns"] = ["*.default.svc.cluster.local."]
    elif kind == "TimeChaos":
        unexpected = set(params) - {"offset"}
        if unexpected:
            raise ValueError(f"unsupported time parameters: {sorted(unexpected)}")
        offset_seconds = _duration_seconds(params.get("offset", "+300s"), signed=True)
        if abs(offset_seconds) > 300:
            raise ValueError("time offset must be within +/-5 minutes")
        document["spec"]["timeOffset"] = str(params.get("offset") or "+300s")

    return document


def _fault_record(document: dict) -> dict:
    """Return the validated model-level fault without hostile extra fields."""
    spec = document["spec"]
    kind = document["kind"]
    params: dict[str, Any] = {"duration": spec["duration"]}
    if kind == "StressChaos":
        action = next(iter(spec["stressors"]))
        params.update(spec["stressors"][action])
    elif kind == "TimeChaos":
        action = "offset"
        params["offset"] = spec["timeOffset"]
    else:
        action = spec["action"]
        if kind == "NetworkChaos" and action == "delay":
            params.update(spec["delay"])
        elif kind == "NetworkChaos" and action != "partition":
            params[action] = int(spec[action][action])
    return {
        "kind": kind,
        "action": action,
        "target_service": spec["selector"]["labelSelectors"]["app"],
        "params": params,
    }


def _fault_to_yaml(fault: dict, scenario_id: str, index: int) -> str:
    """Serialize one allowlisted fault without interpolating model-supplied YAML."""
    document = _fault_document(fault, scenario_id, index)
    return yaml.safe_dump(document, sort_keys=False, default_flow_style=False).rstrip()


def _safe_scenario_id(raw_id: object) -> str:
    candidate = str(raw_id or "").strip()
    if not _SAFE_SCENARIO_ID.fullmatch(candidate):
        raise ValueError(
            f"unsafe adversarial scenario_id (use 1-64 letters/digits/dot/underscore/hyphen): {candidate!r}"
        )
    return candidate


def _adversarial_paths(scenario_id: str) -> tuple[Path, Path]:
    root = ADVERSARIAL_DIR.resolve()
    manifest_path = (ADVERSARIAL_DIR / f"{scenario_id}.yaml").resolve()
    metadata_path = (ADVERSARIAL_DIR / f"{scenario_id}.json").resolve()
    if manifest_path.parent != root or metadata_path.parent != root:
        raise ValueError("adversarial scenario path escapes the generated-scenario directory")
    if manifest_path.exists() or metadata_path.exists():
        raise FileExistsError(
            f"refusing to overwrite generated adversarial evidence: {scenario_id}"
        )
    return manifest_path, metadata_path


async def design_scenario(failure_history: list[dict]) -> dict[str, Any]:
    """Generate one unique adversarial scenario targeting the agent's weaknesses."""
    weaknesses = _extract_weaknesses(failure_history)

    prompt = f"""Agent failure history summary:
- Failed scenarios: {[e.get('scenario_id') for e in failure_history if not e.get('resolved')]}
- Top weaknesses identified: {weaknesses}
- Total episodes analysed: {len(failure_history)}

Available fault primitives: {AVAILABLE_PRIMITIVES}
Available target services: {SERVICES}

Design a NEW adversarial chaos scenario that specifically targets: {weaknesses[0] if weaknesses else 'general multi-fault handling'}

Make it {('extreme' if len(failure_history) > 20 else 'expert' if len(failure_history) > 10 else 'hard')} difficulty."""

    raw = await _call_judge(prompt)
    if len(raw) > 65536:
        raise ValueError("adversarial judge response exceeds 64 KiB")

    # Parse JSON from response
    try:
        start = raw.find("{")
        end   = raw.rfind("}") + 1
        spec  = json.loads(raw[start:end])
    except (json.JSONDecodeError, ValueError):
        # Fallback: create a generic hard scenario
        spec = {
            "scenario_id": f"adv-fallback-{uuid.uuid4().hex[:6]}",
            "title": "Multi-fault cascade with red herring",
            "difficulty": "hard",
            "faults": [
                {"kind": "NetworkChaos", "action": "delay", "target_service": "currencyservice",
                 "params": {"latency": "2000ms", "duration": "15m"}},
                {"kind": "PodChaos", "action": "pod-kill", "target_service": "redis-cart",
                 "params": {}},
            ],
        }

    if not isinstance(spec, dict):
        raise ValueError("adversarial judge response must be a JSON object")
    title = spec.get("title", "Adversarial scenario")
    difficulty = spec.get("difficulty", "hard")
    if not isinstance(title, str) or len(title) > 200:
        raise ValueError("adversarial title must be a string of at most 200 characters")
    if difficulty not in {"hard", "expert", "extreme"}:
        raise ValueError(f"unsupported adversarial difficulty: {difficulty!r}")
    faults = spec.get("faults")
    if not isinstance(faults, list) or not 1 <= len(faults) <= 4:
        raise ValueError("adversarial scenario must contain 1-4 faults")

    def bounded_text_list(value: object, field: str) -> list[str]:
        if not isinstance(value, list) or len(value) > 10:
            raise ValueError(f"adversarial {field} must contain at most 10 strings")
        if any(not isinstance(item, str) or not item.strip() or len(item) > 200 for item in value):
            raise ValueError(f"adversarial {field} contains an invalid string")
        return [item.strip() for item in value]

    root_cause_chain = bounded_text_list(spec.get("root_cause_chain", []), "root_cause_chain")
    red_herrings = bounded_text_list(spec.get("red_herrings", []), "red_herrings")

    # Model-supplied identity is untrusted and must never select an arbitrary path.
    scenario_id = _safe_scenario_id(
        spec.get("scenario_id") or f"adv-{uuid.uuid4().hex[:8]}"
    )
    clean_spec = {
        "scenario_id": scenario_id,
        "title": title.strip(),
        "difficulty": difficulty,
        "root_cause_chain": root_cause_chain,
        "red_herrings": red_herrings,
        "weaknesses_targeted": weaknesses,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "faults": faults,
    }

    documents = [
        _fault_document(fault, scenario_id, index)
        for index, fault in enumerate(clean_spec["faults"])
    ]
    clean_spec["faults"] = [_fault_record(document) for document in documents]
    manifest = yaml.safe_dump_all(
        documents,
        explicit_start=True,
        sort_keys=False,
        default_flow_style=False,
    )

    manifest_path, metadata_path = _adversarial_paths(scenario_id)
    manifest_path.write_text(manifest, encoding="utf-8")

    # Write metadata sidecar
    metadata_path.write_text(json.dumps(clean_spec, indent=2), encoding="utf-8")

    return {
        "scenario_id": scenario_id,
        "manifest_path": str(manifest_path),
        "spec": clean_spec,
    }


async def design_batch(failure_history: list[dict], count: int = 10) -> list[dict]:
    """Generate `count` unique adversarial scenarios in one session."""
    results = []
    for i in range(count):
        # Pass growing history so each scenario is harder than the last
        result = await design_scenario(failure_history)
        results.append(result)
        print(f"[{i+1}/{count}] Generated: {result['scenario_id']} "
              f"({result['spec'].get('difficulty', '?')}) → {result['manifest_path']}")
    return results
