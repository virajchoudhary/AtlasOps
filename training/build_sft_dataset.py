"""AtlasOps SFT Dataset Builder and Training Manifest Generator (Gate G7).

Generates the canonical training-only SFT trajectory corpus (data/sft_corpus_train.jsonl),
strictly enforcing zero test-set leakage, validating schema compliance, verifying
Qwen2.5 template renderability, and persisting the dataset manifest.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

from config.scenario_catalog import SCENARIO_CATALOG, ScenarioMetadata
from config.splits import TEST_SPLIT, VAL_SPLIT, get_split
from training.generate_trajectories import SFT_EXAMPLE_FORMAT, trajectory_to_sft_examples
from training.sft_rendering import prepare_example_for_training, render_messages
from training.sft_provenance import has_redirecting_path_component

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("build_sft_dataset")

DATA_DIR = Path("data")
EVIDENCE_DIR = Path("artifacts/evidence/stage7")


def create_expert_trajectory(scenario_id: str, meta: ScenarioMetadata) -> dict[str, Any]:
    """Synthesize a canonical expert multi-agent incident trajectory for an SFT training scenario."""
    target_svc = meta.target_services[0] if meta.target_services else "frontend"
    expected_alert = meta.expected_alert
    expected_root_cause = meta.expected_root_cause

    # 1. Triage Agent Expert Trajectory
    triage_trajectory = [
        {
            "role": "triage",
            "turn": 0,
            "tool": "alertmanager_list_alerts",
            "args": {"active_only": True},
            "output": {
                "success": True,
                "count": 1,
                "alerts": [
                    {
                        "alertname": expected_alert,
                        "status": "firing",
                        "severity": "critical",
                        "namespace": "default",
                        "service": target_svc,
                    }
                ],
            },
        }
    ]
    triage_final = {
        "severity": "P1",
        "impact": f"High severity outage affecting {target_svc} in default namespace.",
        "affected_services": list(meta.target_services),
        "recommended_action": f"Route to diagnosis for immediate {target_svc} investigation.",
    }

    # 2. Diagnosis Agent Expert Trajectory
    diag_trajectory = [
        {
            "role": "diagnosis",
            "turn": 0,
            "tool": "promql_query",
            "args": {"query": f'rate(http_requests_total{{service="{target_svc}",status=~"5.."}}[5m])'},
            "output": {"success": True, "result": [{"metric": {"service": target_svc}, "value": [1725000000, "14.2"]}]},
        },
        {
            "role": "diagnosis",
            "turn": 1,
            "tool": "kubectl_describe",
            "args": {"namespace": "default", "resource_type": "pod", "resource_name": f"{target_svc}-primary"},
            "output": {"success": True, "details": f"Pod {target_svc} exhibits fault condition matching {meta.chaos_kinds[0]}."},
        },
    ]
    diag_final = {
        "root_cause": expected_root_cause,
        "fault_domain": meta.tier,
        "affected_workload": target_svc,
        "confidence": 0.95,
        "recommended_remediation": f"Remediate {target_svc} failure vector and verify service health.",
    }

    # 3. Remediation Agent Expert Trajectory
    remed_trajectory = [
        {
            "role": "remediation",
            "turn": 0,
            "tool": "k8s_delete_pod",
            "args": {"namespace": "default", "pod_name": f"{target_svc}-failing-pod"},
            "output": {"success": True, "message": f"Pod {target_svc}-failing-pod deleted, replica replacement scheduled."},
        },
        {
            "role": "remediation",
            "turn": 1,
            "tool": "environment_verify",
            "args": {"scenario_id": scenario_id, "require_chaos_cleared": meta.require_chaos_cleared},
            "output": {"success": True, "env_resolved": True, "chaos_mesh_cleared": True},
        },
    ]
    remed_final = {
        "status": "resolved",
        "outcome": "resolved",
        "actions_taken": [
            {"tool": "k8s_delete_pod", "target": target_svc, "status": "success"},
            {"tool": "environment_verify", "target": scenario_id, "status": "success"},
        ],
        "time_to_resolve_seconds": 38.0,
    }

    # 4. Comms Agent Expert Trajectory
    postmortem_path = f"artifacts/postmortems/INC-{scenario_id.replace('/', '-')}.md"
    comms_trajectory = [
        {"role": "comms", "turn": 0, "content": f"Resolved incident for {scenario_id}. Postmortem compiled."}
    ]
    comms_final = {
        "postmortem_path": postmortem_path,
        "executive_summary": f"Incident {expected_alert} targeting {target_svc} successfully diagnosed as '{expected_root_cause}' and remediated.",
        "root_cause": expected_root_cause,
        "timeline": [{"time": "T0", "event": "Alert firing"}, {"time": "T+38s", "event": "Resolution verified"}],
    }

    incident = {
        "incident_id": f"inc-{scenario_id.replace('/', '-')}",
        "scenario_id": scenario_id,
        "tier": meta.tier,
        "triage": {"input": {"scenario_id": scenario_id, "alert": {"alertname": expected_alert}}, "trajectory": triage_trajectory, "final": triage_final},
        "diagnosis": {"input": {"scenario_id": scenario_id, "triage": triage_final}, "trajectory": diag_trajectory, "final": diag_final},
        "remediation": {"input": {"scenario_id": scenario_id, "diagnosis": diag_final}, "trajectory": remed_trajectory, "final": remed_final},
        "comms": {"input": {"scenario_id": scenario_id, "remediation": remed_final}, "trajectory": comms_trajectory, "final": comms_final},
    }

    return incident


def _validate_generated_paths(out_file: Path, canonical_file: Path) -> None:
    evidence_dir = EVIDENCE_DIR.resolve()
    is_canonical = out_file.resolve() == canonical_file.resolve()
    if not is_canonical and out_file.resolve().is_relative_to(evidence_dir):
        raise ValueError("Custom corpus output cannot be inside canonical Stage 7 evidence")

    for path in (
        out_file,
        out_file.parent / "sft_corpus_manifest.json",
        out_file.parent / "sft_training_config.json",
    ):
        if has_redirecting_path_component(path):
            raise ValueError(
                f"Generated corpus output cannot use a symlink, junction, or hard link: {path}"
            )
        if path.resolve().is_relative_to(evidence_dir):
            raise ValueError(
                f"Generated corpus output cannot target canonical Stage 7 evidence: {path}"
            )


def _validate_generated_parents(paths: tuple[Path, ...]) -> None:
    evidence_dir = EVIDENCE_DIR.resolve()
    for path in paths:
        if has_redirecting_path_component(path.parent):
            raise ValueError(f"Generated corpus parent path was redirected: {path.parent}")
        if path.parent.resolve().is_relative_to(evidence_dir):
            raise ValueError(
                f"Generated corpus output cannot target canonical Stage 7 evidence: {path}"
            )


def _stage_text_output(
    destination: Path,
    content: str,
    staged_paths: list[Path],
) -> Path:
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline=None,
        dir=destination.parent,
        prefix=f".{destination.name}.",
        suffix=".tmp",
        delete=False,
    ) as stream:
        staged_path = Path(stream.name)
        staged_paths.append(staged_path)
        stream.write(content)
        stream.flush()
        os.fsync(stream.fileno())
    return staged_path


def build_sft_corpus(output_path: Path | None = None) -> tuple[Path, dict[str, Any]]:
    """Generate the complete SFT training corpus strictly bounded to TRAIN_SPLIT."""
    train_ids = get_split("train")
    val_set = set(VAL_SPLIT)
    test_set = set(TEST_SPLIT)

    # 1. Enforce strict split isolation
    for sid in train_ids:
        if sid in val_set:
            raise ValueError(f"LEAKAGE DETECTED: Scenario {sid} is in VAL_SPLIT!")
        if sid in test_set:
            raise ValueError(f"LEAKAGE DETECTED: Scenario {sid} is in TEST_SPLIT!")

    canonical_file = DATA_DIR / "sft_corpus_train.jsonl"
    out_file = output_path or canonical_file
    manifest_path = out_file.parent / "sft_corpus_manifest.json"
    config_path = out_file.parent / "sft_training_config.json"
    _validate_generated_paths(out_file, canonical_file)
    out_file.parent.mkdir(parents=True, exist_ok=True)
    _validate_generated_paths(out_file, canonical_file)

    examples: list[dict[str, Any]] = []
    judge_score = {"correctness": 1.0, "efficiency": 0.95, "reasoning": 0.95, "red_herring_handling": 1.0, "overall": 0.98, "critique": "Optimal SRE execution."}
    reward_contract = {"total": 0.96, "r_resolve": 0.35, "r_speed": 0.15, "r_evidence": 0.20, "r_safety": 0.15, "r_comms": 0.11, "penalty_total": 0.0}

    log.info("Building SFT training corpus for %d scenarios in TRAIN_SPLIT...", len(train_ids))

    generated_paths = (out_file, manifest_path, config_path)
    staged_paths: list[Path] = []
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            newline=None,
            dir=out_file.parent,
            prefix=f".{out_file.name}.",
            suffix=".tmp",
            delete=False,
        ) as corpus_stream:
            corpus_temp_path = Path(corpus_stream.name)
            staged_paths.append(corpus_temp_path)
            for sid in train_ids:
                meta = SCENARIO_CATALOG[sid]
                incident = create_expert_trajectory(sid, meta)
                sft_examples = trajectory_to_sft_examples(
                    sid,
                    meta.tier,
                    incident,
                    judge_score,
                    reward_contract,
                )

                for ex in sft_examples:
                    prepared = prepare_example_for_training(ex)
                    rendered_text, gen_spans = render_messages(
                        prepared["messages"],
                        tools=prepared["tools"],
                        track_generation=True,
                    )
                    assert rendered_text, (
                        f"Template rendering failed for {ex['scenario_id']} role={ex['role']}"
                    )
                    assert len(gen_spans) > 0, (
                        f"No generation spans found for {ex['scenario_id']} role={ex['role']}"
                    )
                    corpus_stream.write(json.dumps(ex) + "\n")
                    examples.append(ex)
            corpus_stream.flush()
            os.fsync(corpus_stream.fileno())

        # Hash and publish the exact staged corpus bytes; the final path is replaced atomically.
        raw_bytes = corpus_temp_path.read_bytes()
        corpus_sha256 = hashlib.sha256(raw_bytes.replace(b"\r\n", b"\n")).hexdigest()
        role_counts = {
            role: sum(1 for example in examples if example["role"] == role)
            for role in ("triage", "diagnosis", "remediation", "comms")
        }
        tier_counts = {
            tier: sum(1 for example in examples if example["tier"] == tier)
            for tier in ("single_fault", "cascade", "multi_fault", "named_replays")
        }
        total_tool_turns = sum(example["n_tool_turns"] for example in examples)

        manifest = {
            "dataset_name": "atlasops_sft_corpus_train",
            "format": SFT_EXAMPLE_FORMAT,
            "data_origin": "scenario_derived_synthetic",
            "synthetic": True,
            "corpus_file": str(out_file.as_posix()),
            "corpus_sha256_canonical_lf": corpus_sha256,
            "total_examples": len(examples),
            "total_scenarios": len(train_ids),
            "split": "train",
            "quarantined_splits": ["val", "test"],
            "leakage_verified": True,
            "role_distribution": role_counts,
            "tier_distribution": tier_counts,
            "total_tool_turns": total_tool_turns,
            "template_render_validated": True,
        }

        training_config = {
            "base_model": "Qwen/Qwen2.5-7B-Instruct",
            "quantization": "4-bit NF4",
            "lora_r": 16,
            "lora_alpha": 32,
            "lora_dropout": 0.05,
            "target_modules": [
                "q_proj",
                "k_proj",
                "v_proj",
                "o_proj",
                "gate_proj",
                "up_proj",
                "down_proj",
            ],
            "learning_rate": 2e-4,
            "batch_size": 2,
            "gradient_accumulation_steps": 4,
            "max_seq_length": 2048,
            "num_train_epochs": 3,
            "optimizer": "paged_adamw_8bit",
            "assistant_only_loss": True,
            "train_corpus_sha256": corpus_sha256,
        }

        staged_manifest = _stage_text_output(
            manifest_path,
            json.dumps(manifest, indent=2),
            staged_paths,
        )
        staged_config = _stage_text_output(
            config_path,
            json.dumps(training_config, indent=2),
            staged_paths,
        )

        _validate_generated_parents(generated_paths)
        os.replace(corpus_temp_path, out_file)
        _validate_generated_parents(generated_paths)
        os.replace(staged_manifest, manifest_path)
        _validate_generated_parents(generated_paths)
        os.replace(staged_config, config_path)

        log.info(
            "SFT Corpus successfully assembled! Total examples: %d, SHA-256: %s",
            len(examples),
            corpus_sha256,
        )
        return out_file, manifest
    finally:
        for staged_path in staged_paths:
            staged_path.unlink(missing_ok=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build AtlasOps SFT Training Corpus (Gate G7)")
    parser.add_argument("--output", default="data/sft_corpus_train.jsonl", help="Output corpus path")
    args = parser.parse_args()
    build_sft_corpus(Path(args.output))


if __name__ == "__main__":
    main()
