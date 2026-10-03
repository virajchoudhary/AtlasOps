import hashlib
import json
from pathlib import Path

from config.splits import get_split
from scripts.verify_base_sft_validation import (
    canonical_hash, recompute, validate_identity,
)

ROOT = Path(__file__).resolve().parents[1] / "artifacts/evidence/stage8/base-sft-validation-v1"
RUN = ROOT / "base-sft-validation-20261003-v1"


def test_preserved_real_validation_evidence_integrity_and_scores():
    manifest_raw = (RUN / "run_manifest.json").read_bytes()
    assert hashlib.sha256(manifest_raw).hexdigest() == (
        "878b01e079d294ad35a6ab73365c01e1a3d7cf138a2afaae72b73ecbb59d49b4"
    )
    manifest = json.loads(manifest_raw)
    assert manifest["status"] == "completed"
    assert manifest["source"] == {
        "git_sha": "3808849125db0ddfb6bf64fe853dffe3fce608c1", "git_dirty": False,
    }
    assert manifest["scenario_ids"] == list(get_split("val"))
    assert manifest["split_sha256"] == canonical_hash(list(get_split("val")))
    for filename, key in (
        ("raw_episodes.jsonl", "raw_episodes_sha256"),
        ("episodes.jsonl", "episodes_sha256"),
        ("summary.json", "summary_sha256"),
    ):
        assert hashlib.sha256((RUN / filename).read_bytes()).hexdigest() == (
            manifest["artifacts"][key]
        )
    rows = [json.loads(line) for line in (RUN / "episodes.jsonl").read_bytes().splitlines()]
    validate_identity(manifest, rows)
    assert len(rows) == 12
    assert all(row["response_received"] and row["generated_token_ids"] for row in rows)
    references = {row["scenario_id"]: row["scoring_reference"]["expected_root_cause"]
                  for row in rows}
    metrics = recompute(rows, list(get_split("val")), references)
    assert metrics["paired"]["both_scored_count"] == 6
    assert metrics["paired"]["mean_sft_minus_base_diagnostic_f1"] == -0.0094
    summary = json.loads((RUN / "summary.json").read_bytes())
    assert summary["per_arm"]["base"]["avg_diagnostic_f1"] == 0.16875
    assert summary["per_arm"]["sft"]["avg_diagnostic_f1"] == 0.15935
    assert summary["resolution_rate"] is None
    assert summary["avg_time_to_resolve_s"] is None
    assert summary["claims"]["g6_or_g8_gate_closure"] is False
