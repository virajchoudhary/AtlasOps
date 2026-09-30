"""Candidate admission tests are entirely synthetic and non-executing."""

import copy
import json
import sys

import pytest

from config.splits import TRAIN_SPLIT, VAL_SPLIT
from training import sft_candidate
from training.sft_provenance import snapshot_training_corpus


@pytest.fixture
def rows():
    from training.build_sft_candidate import build_candidate_rows

    return build_candidate_rows()


def test_candidate_validates_without_mutation(rows):
    before = copy.deepcopy(rows)
    report = sft_candidate.inspect_candidate_rows(rows)
    assert rows == before
    assert report["technical_admissibility"] == "PASS"
    assert report["d3_approval"] == "PENDING"
    assert report["total_scenarios"] == len(TRAIN_SPLIT)


@pytest.mark.parametrize(
    "change",
    [
        "split", "duplicate", "tool_name", "pairing", "schema", "injected_label",
        "escaped_label", "normalized_label", "nonfinite", "falsey_calls", "delimiter", "approval",
        "outcome", "unbound_verifier", "empty_executed",
    ],
)
def test_candidate_rejects_corruption(rows, change):
    tool_row = next(r for r in rows if any(m.get("tool_calls") for m in r["messages"]))
    call_message = next(m for m in tool_row["messages"] if m.get("tool_calls"))
    if change == "split":
        rows[0]["scenario_id"] = VAL_SPLIT[0]
    elif change == "duplicate":
        rows.append(copy.deepcopy(rows[0]))
    elif change == "tool_name":
        call_message["tool_calls"][0]["function"]["name"] = "environment_verify"
    elif change == "pairing":
        tool_row["messages"] = [m for m in tool_row["messages"] if m["role"] != "tool"]
    elif change == "schema":
        call_message["tool_calls"][0]["function"]["arguments"] = '{"unexposed":true}'
    elif change == "injected_label":
        context = json.loads(tool_row["messages"][1]["content"])
        context["expected_root_cause"] = "do not leak catalog labels"
        tool_row["messages"][1]["content"] = json.dumps(context)
    elif change == "escaped_label":
        context = json.loads(tool_row["messages"][1]["content"])
        context["ground_truth"] = "not allowed"
        tool_row["messages"][1]["content"] = json.dumps(context).replace(
            "ground_truth", "\\u0067round_truth"
        )
    elif change == "normalized_label":
        context = json.loads(tool_row["messages"][1]["content"])
        context[" Ground_Truth "] = "not allowed"
        tool_row["messages"][1]["content"] = json.dumps(context)
    elif change == "nonfinite":
        context = json.loads(tool_row["messages"][1]["content"])
        context["measurement"] = float("nan")
        tool_row["messages"][1]["content"] = json.dumps(context)
    elif change == "falsey_calls":
        tool_row["messages"][-1]["tool_calls"] = {}
    elif change == "delimiter":
        tool_row["messages"][-1]["content"] += "<|im_end|>"
    elif change == "approval":
        row = next(r for r in rows if r["safety"]["approval"]["status"] == "approved")
        row["safety"]["approval"]["approved_by"] = ""
    elif change == "outcome":
        rows[0]["outcome"] = "fabricated"
    elif change == "unbound_verifier":
        row = next(r for r in rows if r["role"] == "remediation" and r["outcome"] == "successful")
        update = next(m for m in row["messages"] if m["role"] == "tool")
        observation = json.loads(update["content"])
        observation["verifier_observation"]["tool_call_id"] = "unpaired"
        update["content"] = json.dumps(observation)
    elif change == "empty_executed":
        row = next(r for r in rows if r["role"] == "remediation" and r["outcome"] == "successful")
        final = json.loads(row["messages"][-1]["content"])
        final["executed_actions"] = []
        row["messages"][-1]["content"] = json.dumps(final)
    with pytest.raises((ValueError, TypeError)):
        sft_candidate.inspect_candidate_rows(rows)


def test_nontrain_catalog_is_never_indexed(monkeypatch):
    from config import scenario_catalog
    from training import build_sft_candidate

    original = scenario_catalog.SCENARIO_CATALOG

    class TrainOnlyCatalog(dict):
        def __getitem__(self, sid):
            assert sid in TRAIN_SPLIT
            return original[sid]

        def values(self):
            raise AssertionError("full catalog inspection forbidden")

        def items(self):
            raise AssertionError("full catalog inspection forbidden")

    guarded = TrainOnlyCatalog()
    monkeypatch.setattr(scenario_catalog, "SCENARIO_CATALOG", guarded)
    if hasattr(build_sft_candidate, "SCENARIO_CATALOG"):
        monkeypatch.setattr(build_sft_candidate, "SCENARIO_CATALOG", guarded)
    rows = build_sft_candidate.build_candidate_rows()
    assert {r["scenario_id"] for r in rows} == set(TRAIN_SPLIT)


def test_manifest_tamper_and_pending_d3_refused_before_output(rows, tmp_path, monkeypatch):
    from training import sft
    from training.build_sft_candidate import serialize_candidate_rows

    monkeypatch.setattr(sft_candidate, "validate_source_revision", lambda _manifest: None)
    raw = serialize_candidate_rows(rows)
    corpus = tmp_path / "train.jsonl"
    corpus.write_bytes(raw)
    manifest = sft_candidate.candidate_manifest(rows, raw, "a" * 40)
    sidecar = tmp_path / "sft_corpus_manifest.json"
    sidecar.write_text(json.dumps(manifest), encoding="utf-8")
    snapshot = snapshot_training_corpus(corpus)
    assert sft_candidate.validate_candidate_snapshot(snapshot, manifest)["d3_approval"] == "PENDING"
    changed = copy.deepcopy(manifest)
    changed["d3_approval"] = "APPROVED"
    with pytest.raises(ValueError, match="manifest"):
        sft_candidate.validate_candidate_snapshot(snapshot, changed)
    output = tmp_path / "checkpoint"
    monkeypatch.setattr(sys, "argv", [
        "sft.py", "--model", "Qwen/Qwen2.5-7B-Instruct",
        "--model-revision", "a" * 40, "--data", str(corpus), "--output", str(output),
    ])
    with pytest.raises(ValueError, match="D3 approval PENDING"):
        sft.main()
    assert not output.exists()


def test_unversioned_rows_are_not_a_training_bypass(tmp_path):
    corpus = tmp_path / "train.jsonl"
    corpus.write_text("".join(
        json.dumps({"scenario_id": sid, "role": "triage"}) + "\n"
        for sid in TRAIN_SPLIT
    ), encoding="utf-8")
    with pytest.raises(ValueError, match="no versioned"):
        sft_candidate.refuse_unapproved_candidate(snapshot_training_corpus(corpus))


def test_source_revision_must_contain_generator_and_exact_hashes(monkeypatch):
    import subprocess

    def absent(*_args, **_kwargs):
        raise subprocess.CalledProcessError(128, ["git"])

    monkeypatch.setattr(sft_candidate.subprocess, "run", absent)
    with pytest.raises(ValueError, match="immutable source"):
        sft_candidate.validate_source_revision({"source_git_sha": "a" * 40})


def test_stored_candidate_is_reproducible_and_review_only():
    from training.sft_provenance import REPO_ROOT

    corpus = REPO_ROOT / "artifacts/evidence/stage7/candidates/train-candidate-v1/sft_corpus_train.jsonl"
    if not corpus.exists():
        pytest.skip("versioned review bundle not yet frozen")
    snapshot = snapshot_training_corpus(corpus)
    manifest = sft_candidate.read_candidate_manifest(corpus)
    report = sft_candidate.validate_candidate_snapshot(snapshot, manifest)
    assert report["d3_approval"] == "PENDING"
    assert report["technical_admissibility"] == "PASS"
    with pytest.raises(ValueError, match="D3 approval PENDING"):
        sft_candidate.refuse_unapproved_candidate(snapshot)
