"""RS CLI output isolation; only synthetic local fixtures are evaluated."""

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from recommender._cli import fresh_output_directory
from recommender.dataset import build_incident_interactions, load_interactions
from recommender.evaluate import run_full_baseline_benchmark

ROOT = Path(__file__).resolve().parents[1]
MODULES = ("recommender.dataset", "recommender.evaluate", "recommender.train_hybrid")


@pytest.mark.parametrize("kind", ["file", "directory_link"])
def test_output_admission_rejects_existing_files_and_links(kind, tmp_path):
    import argparse

    destination = tmp_path / "destination"
    if kind == "file":
        destination.write_bytes(b"retained")
    else:
        target = tmp_path / "target"
        target.mkdir()
        (target / "retained").write_bytes(b"untouched")
        try:
            destination.symlink_to(target, target_is_directory=True)
        except OSError:
            pytest.skip("Directory symlinks unavailable")
    with pytest.raises(SystemExit) as exc:
        fresh_output_directory(argparse.ArgumentParser(), destination)
    assert exc.value.code == 2
    if kind == "file":
        assert destination.read_bytes() == b"retained"
    else:
        assert destination.is_symlink()
        assert (target / "retained").read_bytes() == b"untouched"


def run_cli(module, cwd, *args):
    return subprocess.run(
        [sys.executable, "-B", "-m", module, *map(str, args)],
        cwd=cwd,
        env={**os.environ, "PYTHONPATH": str(ROOT)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


@pytest.mark.parametrize("module", MODULES)
def test_cli_without_explicit_paths_cannot_generate_or_overwrite(module, tmp_path):
    result = run_cli(module, tmp_path)
    assert result.returncode == 2
    assert "--output-dir" in result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("module", MODULES)
def test_existing_output_directory_is_preserved(module, tmp_path):
    source, _ = build_incident_interactions(output_path=tmp_path / "source.jsonl")
    output = tmp_path / "retained"
    output.mkdir()
    sentinel = output / "historical.json"
    sentinel.write_bytes(b"preserved negative evidence")
    args = ["--output-dir", output]
    if module != "recommender.dataset":
        args.extend(["--input", source])
    result = run_cli(module, tmp_path, *args)
    assert result.returncode == 2
    assert "fresh output directory" in result.stderr
    assert {p.name: p.read_bytes() for p in output.iterdir()} == {
        "historical.json": b"preserved negative evidence"
    }


@pytest.mark.parametrize("module", MODULES[1:])
def test_missing_input_is_not_generated_and_output_is_not_created(module, tmp_path):
    result = run_cli(
        module, tmp_path, "--input", tmp_path / "missing.jsonl",
        "--output-dir", tmp_path / "result",
    )
    assert result.returncode == 2
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    ("module", "filenames"),
    [
        ("recommender.dataset", {"interactions.jsonl", "interactions.manifest.json"}),
        ("recommender.evaluate", {"baseline_eval.json"}),
        ("recommender.train_hybrid", {"hybrid_recommender.json", "hybrid_eval.json"}),
    ],
)
def test_cli_writes_only_to_fresh_explicit_directory(module, filenames, tmp_path):
    source, _ = build_incident_interactions(output_path=tmp_path / "source.jsonl")
    before = {p.name: p.read_bytes() for p in tmp_path.iterdir()}
    output = tmp_path / "result"
    args = ["--output-dir", output]
    if module != "recommender.dataset":
        args.extend(["--input", source])
    result = run_cli(module, tmp_path, *args)
    assert result.returncode == 0, result.stderr
    assert {p.name for p in output.iterdir()} == filenames
    for p in output.iterdir():
        if p.suffix == ".json":
            assert isinstance(json.loads(p.read_text()), dict)
    assert {p.name: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()} == before
    assert {p.name for p in tmp_path.iterdir()} == set(before) | {"result"}


def test_explicit_missing_input_loader_never_generates(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_interactions(tmp_path / "missing.jsonl", generate_if_missing=False)
    assert list(tmp_path.iterdir()) == []


def test_empty_explicit_baseline_corpus_does_not_load_defaults(tmp_path, monkeypatch):
    monkeypatch.setattr(
        "recommender.evaluate.load_interactions",
        lambda: pytest.fail("implicit default corpus loaded"),
    )
    result = run_full_baseline_benchmark([], output_path=tmp_path / "result.json")
    assert result["dataset_split_counts"]["total"] == 0
