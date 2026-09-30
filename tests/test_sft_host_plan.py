"""Static non-live checks of named-host preparation artifacts."""

import hashlib
import json
import math
from pathlib import Path

from training.sft_pilot_gate import (
    BASE_MODEL, CORPUS_HASH, EXECUTION_APPROVAL_SHA256, PLAN_PATH, PLAN_SHA256, REVISION,
)

ROOT = Path(__file__).resolve().parents[1]


def test_pilot_identity_and_execution_boundary_remain_unchanged():
    raw = PLAN_PATH.read_bytes()
    plan = json.loads(raw)
    assert hashlib.sha256(raw).hexdigest() == PLAN_SHA256
    assert plan["model"] == BASE_MODEL
    assert plan["model_revision"] == plan["tokenizer_revision"] == REVISION
    assert plan["corpus_sha256"] == CORPUS_HASH
    assert plan["d3_status"] == "APPROVED_FOR_PREPARATION"
    assert plan["execution_allowed"] is False
    assert EXECUTION_APPROVAL_SHA256 is None
    assert len(plan["environment"]["package_versions"]) == 72


def test_public_weight_metadata_is_not_a_weight_download_or_approval():
    metadata = json.loads(
        (ROOT / "artifacts/evidence/stage7/qwen_a09a354_weight_metadata_v1.json").read_text()
    )
    assert metadata["repository"] == BASE_MODEL and metadata["revision"] == REVISION
    assert metadata["weights_downloaded"] is False
    assert len(metadata["shards"]) == 4
    assert sum(row["size_bytes"] for row in metadata["shards"].values()) == 15231271888
    assert metadata["total_weight_bytes"] == 15231271888
    assert all(
        len(row["sha256"]) == 64 and name.endswith(".safetensors")
        for name, row in metadata["shards"].items()
    )


def test_host_image_has_pinned_git_source_and_inert_default():
    recipe = (ROOT / "infra/training/sft-pilot/Dockerfile.host-v1").read_text()
    original = (ROOT / "infra/training/sft-pilot/Dockerfile").read_text()
    assert recipe.splitlines()[0] == original.splitlines()[0]
    assert "https://snapshot.debian.org/archive/debian/20260930T000000Z" in recipe
    assert "https://snapshot.debian.org/archive/debian-security/20260930T000000Z" in recipe
    assert "apt-get install -y --no-install-recommends git ca-certificates openssh-server" in recipe
    assert "dpkg-query -W" in recipe
    assert "--require-hashes" in recipe
    assert 'CMD ["python", "-c", "pass"]' in recipe
    assert "trusted=yes" not in recipe
    assert "train()" not in recipe and "training.sft" not in recipe


def test_explicit_host_startup_only_opens_key_authenticated_ssh():
    script = (ROOT / "infra/training/sft-pilot/start-host.sh").read_text()
    assert "PasswordAuthentication=no" in script
    assert "KbdInteractiveAuthentication=no" in script
    assert "PermitRootLogin=prohibit-password" in script
    assert "AllowTcpForwarding=no" in script
    assert "sshd -D" in script
    assert "training.sft" not in script and "huggingface" not in script


def test_named_resource_cost_proposal_remains_nonexecuting():
    proposal = json.loads((ROOT / "config/sft_remote_host_proposal_v1.json").read_text())
    fallback = proposal["fallback"]
    running = (
        (fallback["volume_disk_gb"] + fallback["container_disk_gb"])
        * fallback["running_storage_rate_usd_gb_month"] / 720
    )
    initial = fallback["gpu_rate_usd_hour"] + running + 54 * 0.2 * 168 / 720
    later = 4 * fallback["gpu_rate_usd_hour"] + 4 * running + 54 * 0.2
    assert math.isclose(initial, fallback["initial_listed_estimate_usd_before_tax"])
    assert math.isclose(later, fallback["later_listed_estimate_usd_before_tax"])
    assert proposal["approval_status"] == "PROPOSED_NOT_APPROVED"
    assert proposal["execution_allowed"] is False
    assert proposal["scope"]["resource_creation_allowed"] is False
    assert fallback["data_center"] == "US-KS-2"
    assert fallback["availability_verified"] is False
    assert fallback["automatic_substitution_allowed"] is False
