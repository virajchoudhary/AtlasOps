from pathlib import Path

import pytest


@pytest.fixture
def prepared_g4_candidate_source_pins(monkeypatch):
    """Model the reviewed source hashes for explicit candidate-contract tests."""
    from config import g4_demo_candidate as candidate

    pinned_sources = candidate.candidate_profile()["causal_evidence_policy"]["source_sha256"]
    actual_file_sha256 = candidate.file_sha256
    source_root = candidate.REPO_ROOT.resolve()

    def file_sha256(path):
        path = Path(path)
        try:
            relative = path.resolve().relative_to(source_root).as_posix()
        except (OSError, ValueError):
            return actual_file_sha256(path)
        if relative in pinned_sources:
            return pinned_sources[relative]
        return actual_file_sha256(path)

    monkeypatch.setattr(candidate, "file_sha256", file_sha256)
    assert candidate.inspect_candidate()["status"] == "PREPARED"
