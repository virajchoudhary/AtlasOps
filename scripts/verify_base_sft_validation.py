"""Independently recompute paired Validation diagnostics from raw responses."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sys
from pathlib import Path

EXPECTED_MANIFEST_SHA256 = "7dd921225fdf267bf32037c138cdc9c7ecd6cbc2686f32dc1d784ac1151d5440"
EXPECTED_ADAPTER_SHA256 = "f20b5ae3fb4db88c0bad89142805b270b28b6ec8e85a981552fc3ee7f5868fff"
EXPECTED_MODEL = "Qwen/Qwen2.5-7B-Instruct"
EXPECTED_REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"

if __package__ in {None, ""}:
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


def canonical_hash(value):
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def unique_keys(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate response JSON key")
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError(f"Nonfinite JSON constant: {value}")


def parse_response(raw):
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, count=1)
        text = re.sub(r"\s*```$", "", text, count=1)
    obj = json.loads(text, object_pairs_hook=unique_keys, parse_constant=reject_constant)
    pending = [(obj, 1)]
    while pending:
        value, depth = pending.pop()
        if depth > 64:
            raise ValueError("Response JSON exceeds depth limit")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError("Nonfinite response value")
        if isinstance(value, dict):
            pending.extend((child, depth + 1) for child in value.values())
        elif isinstance(value, list):
            pending.extend((child, depth + 1) for child in value)
    if not isinstance(obj, dict):
        raise ValueError("Response is not an object")
    if obj.get("severity") not in ("P0", "P1", "P2", "P3"):
        raise ValueError("Invalid severity")
    services = obj.get("affected_services")
    if (
        not isinstance(services, list)
        or not services
        or any(not isinstance(service, str) or not service.strip() for service in services)
    ):
        raise ValueError("Invalid affected services")
    if not isinstance(obj.get("root_cause"), str) or not obj["root_cause"].strip():
        raise ValueError("Invalid root cause")
    confidence = obj.get("confidence")
    if type(confidence) not in (int, float):
        raise ValueError("Invalid confidence")
    try:
        valid = math.isfinite(confidence) and 0 <= confidence <= 1
    except OverflowError:
        valid = False
    if not valid:
        raise ValueError("Invalid confidence")
    return obj


def diagnostic_scores(prediction, truth):
    predicted = set(re.findall(r"\w+", prediction.lower()))
    expected = set(re.findall(r"\w+", truth.lower()))
    if not predicted or not expected:
        score = float(predicted == expected)
        return {"precision": score, "recall": score, "f1": score}
    common = len(predicted & expected)
    precision = common / len(predicted)
    recall = common / len(expected)
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {
        key: round(value, 4)
        for key, value in (("precision", precision), ("recall", recall), ("f1", f1))
    }


def validate_identity(manifest, rows):
    checkpoint = manifest["checkpoint_identity"]
    pin = manifest["input_identity"]["checkpoint_manifest_sha256_pin"]
    if checkpoint["manifest_sha256"] != pin or pin != EXPECTED_MANIFEST_SHA256:
        raise ValueError("Checkpoint does not match the fixed v17 manifest pin")
    weights = [
        record for record in checkpoint["files"] if record["path"] == "adapter_model.safetensors"
    ]
    if len(weights) != 1 or weights[0]["sha256"] != EXPECTED_ADAPTER_SHA256:
        raise ValueError("Checkpoint does not match the fixed v17 adapter pin")
    if manifest["model_identity"] != {
        "id": EXPECTED_MODEL,
        "revision": EXPECTED_REVISION,
        "tokenizer_id": EXPECTED_MODEL,
        "tokenizer_revision": EXPECTED_REVISION,
    }:
        raise ValueError("Model or tokenizer differs from the fixed revision")
    for inventory in (
        manifest["base_snapshot_inventory_before"],
        manifest["base_snapshot_inventory_after"],
    ):
        if inventory["repository"] != EXPECTED_MODEL or inventory["revision"] != EXPECTED_REVISION:
            raise ValueError("Inventory model identity differs from the fixed revision")
    if any(
        row["adapter_state"] != ("disabled" if row["arm"] == "base" else "enabled") for row in rows
    ):
        raise ValueError("Adapter state does not match the evaluation arm")


def recompute(rows, scenario_ids, references):
    if [(row["scenario_id"], row["arm"]) for row in rows] != [
        (scenario, arm) for scenario in scenario_ids for arm in ("base", "sft")
    ]:
        raise ValueError("Missing, duplicated, or reordered paired rows")
    metrics = {}
    for index in range(0, len(rows), 2):
        base, sft = rows[index : index + 2]
        for key in ("request_messages", "prompt_token_ids", "generation_config"):
            if base[key] != sft[key]:
                raise ValueError(f"Pair differs in {key}")
    for arm in ("base", "sft"):
        selected = [row for row in rows if row["arm"] == arm]
        scored = []
        for row in selected:
            try:
                if row.get("response_received") is False:
                    raise ValueError("No model response received")
                prediction = parse_response(row["raw_model_response"] or "")
                valid = True
            except (ValueError, TypeError, OverflowError, RecursionError):
                prediction, valid = None, False
            if row["format_compliant"] is not valid:
                raise ValueError("Recorded schema conformance differs from raw response")
            if valid:
                if row["status"] != "ok" or row["prediction"] != prediction:
                    raise ValueError("Recorded prediction differs from raw response")
                scores = diagnostic_scores(prediction["root_cause"], references[row["scenario_id"]])
                for name, value in scores.items():
                    if row[f"diagnostic_{name}"] != value:
                        raise ValueError("Recorded diagnostic score differs from recomputation")
                scored.append(scores["f1"])
            else:
                if row.get("prediction") is not None or row["status"] == "ok":
                    raise ValueError("Unscorable response has a successful prediction")
                if any(
                    row.get(f"diagnostic_{name}") is not None
                    for name in ("f1", "precision", "recall")
                ):
                    raise ValueError("Unscorable response has a numeric diagnostic score")
        metrics[arm] = {
            "scheduled_count": len(selected),
            "diagnostic_scored_count": len(scored),
            "failed_scenarios": len(selected) - len(scored),
            "diagnostic_schema_conformance_rate": round(len(scored) / len(selected), 4),
            "avg_diagnostic_f1": round(sum(scored) / len(scored), 4) if scored else None,
        }
    paired_deltas = [
        rows[index + 1]["diagnostic_f1"] - rows[index]["diagnostic_f1"]
        for index in range(0, len(rows), 2)
        if rows[index]["status"] == rows[index + 1]["status"] == "ok"
    ]
    metrics["paired"] = {
        "both_scored_count": len(paired_deltas),
        "mean_sft_minus_base_diagnostic_f1": (
            round(sum(paired_deltas) / len(paired_deltas), 4) if paired_deltas else None
        ),
        "sft_minus_base_schema_conformance": round(
            metrics["sft"]["diagnostic_schema_conformance_rate"]
            - metrics["base"]["diagnostic_schema_conformance_rate"],
            4,
        ),
        "basis": "Descriptive Validation diagnostics only; no inferential superiority claim",
    }
    return metrics


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        raise FileExistsError(args.output)
    from config.scenario_catalog import SCENARIO_CATALOG
    from config.splits import get_split

    scenario_ids = list(get_split("val"))
    references = {
        scenario: SCENARIO_CATALOG[scenario].expected_root_cause for scenario in scenario_ids
    }
    raw = (args.run / "episodes.jsonl").read_bytes()
    rows = [json.loads(line) for line in raw.decode().splitlines()]
    manifest = json.loads((args.run / "run_manifest.json").read_bytes())
    if manifest["status"] != "completed":
        raise ValueError("Run did not complete")
    if manifest["split"] != "val" or manifest["scenario_ids"] != scenario_ids:
        raise ValueError("Run does not match ordered frozen Validation split")
    if manifest["split_sha256"] != canonical_hash(scenario_ids):
        raise ValueError("Validation split digest mismatch")
    raw_hash = hashlib.sha256(raw).hexdigest()
    if manifest["raw_predictions_sha256"] != raw_hash:
        raise ValueError("Raw response digest mismatch")
    artifacts = manifest["artifacts"]
    inference_raw = (args.run / "raw_episodes.jsonl").read_bytes()
    if artifacts["raw_episodes_sha256"] != hashlib.sha256(inference_raw).hexdigest():
        raise ValueError("Raw inference ledger digest mismatch")
    unscored = [json.loads(line) for line in inference_raw.decode().splitlines()]
    if len(unscored) != len(rows):
        raise ValueError("Raw and scored row counts differ")
    for source, scored in zip(unscored, rows):
        for key, value in source.items():
            if key not in ("schema_version", "error") and scored.get(key) != value:
                raise ValueError(f"Scored row changed original inference field: {key}")
    checkpoint = manifest["checkpoint_identity"]
    validate_identity(manifest, rows)
    if (
        checkpoint["manifest_sha256"] != checkpoint["manifest_sha256_after"]
        or checkpoint["tree_sha256"] != checkpoint["tree_sha256_after"]
        or manifest["base_snapshot_inventory_before"]["files"]
        != manifest["base_snapshot_inventory_after"]["files"]
    ):
        raise ValueError("Input inventory changed during execution")
    if manifest["source"]["git_dirty"] is not False:
        raise ValueError("Evaluator source was dirty")
    if any(row["run_id"] != manifest["run_id"] for row in rows):
        raise ValueError("Row run identity mismatch")
    expected_generation = {
        "seed": 1337,
        "temperature": 0.0,
        "top_p": 1.0,
        "max_new_tokens": 512,
        "do_sample": False,
    }
    if any(row["generation_config"] != expected_generation for row in rows):
        raise ValueError("Generation settings differ from fixed protocol")
    if any(row["prompt_token_ids"] is None for row in rows):
        raise ValueError("Encoded prompts unavailable")
    for row in rows:
        if (
            row["model_identity"] != manifest["model_identity"]
            or row["checkpoint_identity"]["manifest_sha256"] != checkpoint["manifest_sha256"]
            or row["evaluation_config_sha256"] != manifest["evaluation_config_sha256"]
            or row["prompt_token_ids_sha256"] != canonical_hash(row["prompt_token_ids"])
        ):
            raise ValueError("Row identity or prompt integrity mismatch")
    metrics = recompute(rows, scenario_ids, references)
    summary_raw = (args.run / "summary.json").read_bytes()
    if artifacts["summary_sha256"] != hashlib.sha256(summary_raw).hexdigest():
        raise ValueError("Summary digest mismatch")
    summary = json.loads(summary_raw)
    for arm in ("base", "sft"):
        recorded = summary["per_arm"][arm]
        recomputed = metrics[arm]
        scores = [
            row["diagnostic_f1"] for row in rows if row["arm"] == arm and row["status"] == "ok"
        ]
        expected_mean = round(sum(scores) / len(scores), 6) if scores else None
        expected_rate = round(len(scores) / len(scenario_ids), 6)
        if (
            recorded["avg_diagnostic_f1"] != expected_mean
            or recorded["format_compliance_rate"] != expected_rate
            or recorded["diagnostic_scored_count"] != recomputed["diagnostic_scored_count"]
            or recorded["recorded_count"] != len(scenario_ids)
        ):
            raise ValueError("Summary differs from independently recomputed raw metrics")
    report = {
        "schema_version": "atlasops-base-sft-independent-recompute-v1",
        "status": "PASS",
        "run_id": manifest["run_id"],
        "raw_predictions_sha256": raw_hash,
        "run_manifest_sha256": hashlib.sha256(
            (args.run / "run_manifest.json").read_bytes()
        ).hexdigest(),
        "metrics": metrics,
        "scoring_implementation": "Independent standard-library parser and token-set F1",
        "scope": "Raw-response schema and diagnostic verification; not model execution proof",
        "resolution_rate": None,
        "avg_time_to_resolve_s": None,
    }
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(report, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report, sort_keys=True))


if __name__ == "__main__":
    main()
