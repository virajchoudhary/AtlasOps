"""Validate all Chaos Mesh manifests are syntactically correct YAML
and contain required fields."""

from pathlib import Path
import pytest
import yaml


MANIFESTS_DIR = Path(__file__).parent.parent / "bench" / "chaos_manifests"
FROZEN_TIERS = ("single_fault", "cascade", "multi_fault", "named_replays")

REQUIRED_KINDS = {
    "PodChaos", "NetworkChaos", "StressChaos", "DNSChaos",
    "IOChaos", "TimeChaos",
}


def collect_manifests():
    return list(MANIFESTS_DIR.rglob("*.yaml"))


@pytest.mark.parametrize("manifest_path", collect_manifests())
def test_manifest_is_valid_yaml(manifest_path):
    content = manifest_path.read_text(encoding="utf-8")
    docs = list(yaml.safe_load_all(content))
    assert len(docs) >= 1, f"{manifest_path} produced no YAML documents"


@pytest.mark.parametrize("manifest_path", collect_manifests())
def test_manifest_has_required_fields(manifest_path):
    content = manifest_path.read_text(encoding="utf-8")
    for doc in yaml.safe_load_all(content):
        if doc is None:
            continue
        # Skip non-chaos resources (e.g. Argo CD Application, Deployment)
        if doc.get("kind") not in REQUIRED_KINDS:
            continue
        assert "apiVersion" in doc, f"Missing apiVersion in {manifest_path}"
        assert "metadata" in doc, f"Missing metadata in {manifest_path}"
        assert "spec" in doc, f"Missing spec in {manifest_path}"
        assert "selector" in doc["spec"], f"Missing spec.selector in {manifest_path}"


@pytest.mark.parametrize("manifest_path", collect_manifests())
def test_manifest_no_deprecated_scheduler(manifest_path):
    """spec.scheduler was removed in Chaos Mesh v2 — must not be present."""
    content = manifest_path.read_text(encoding="utf-8")
    for doc in yaml.safe_load_all(content):
        if doc is None or doc.get("kind") not in REQUIRED_KINDS:
            continue
        spec = doc.get("spec", {})
        assert "scheduler" not in spec, (
            f"{manifest_path} uses deprecated spec.scheduler — "
            f"remove it (Chaos Mesh v2 uses spec.duration instead)"
        )


def test_all_tiers_present():
    tiers = {p.parent.name for p in collect_manifests()}
    assert "single_fault" in tiers
    assert "cascade" in tiers
    assert "named_replays" in tiers
    assert "multi_fault" in tiers
    assert "adversarial" in tiers


def test_adversarial_templates_present():
    adv_dir = MANIFESTS_DIR / "adversarial"
    templates = list(adv_dir.glob("adv-*.yaml"))
    assert len(templates) >= 1, "Frozen adv-*.yaml templates should exist for UI + curriculum"


def test_single_fault_count():
    sf = list((MANIFESTS_DIR / "single_fault").glob("*.yaml"))
    assert len(sf) == 8, f"Expected 8 single-fault scenarios, got {len(sf)}"


def test_cascade_count():
    cs = list((MANIFESTS_DIR / "cascade").glob("*.yaml"))
    assert len(cs) == 5, f"Expected 5 cascade scenarios, got {len(cs)}"


def test_named_replays_count():
    nr = list((MANIFESTS_DIR / "named_replays").glob("*.yaml"))
    assert len(nr) == 10, f"Expected 10 named replays, got {len(nr)}"


def test_multi_fault_count():
    mf = list((MANIFESTS_DIR / "multi_fault").glob("*.yaml"))
    assert len(mf) == 5, f"Expected 5 multi-fault scenarios, got {len(mf)}"


def test_frozen_catalogue_matches_filesystem_bijectively():
    from config.runtime import FROZEN_SCENARIOS

    catalogue = set(FROZEN_SCENARIOS)
    files = {
        path.relative_to(MANIFESTS_DIR).with_suffix("").as_posix()
        for tier in FROZEN_TIERS
        for path in (MANIFESTS_DIR / tier).glob("*.yaml")
    }

    assert len(FROZEN_SCENARIOS) == 28
    assert len(catalogue) == len(FROZEN_SCENARIOS)
    assert files == catalogue


def test_adversarial_template_is_not_frozen():
    from config.runtime import FROZEN_SCENARIOS

    assert (MANIFESTS_DIR / "adversarial" / "adv-001.yaml").is_file()
    assert all(not scenario.startswith("adversarial/") for scenario in FROZEN_SCENARIOS)


@pytest.mark.parametrize(
    "scenario_id",
    [
        path.relative_to(MANIFESTS_DIR).with_suffix("").as_posix()
        for tier in FROZEN_TIERS
        for path in (MANIFESTS_DIR / tier).glob("*.yaml")
    ],
)
def test_frozen_manifest_labels_match_catalogue_path(scenario_id):
    tier, scenario_name = scenario_id.split("/", 1)
    path = MANIFESTS_DIR / f"{scenario_id}.yaml"
    labelled_documents = []

    for document in yaml.safe_load_all(path.read_text(encoding="utf-8")):
        if not document:
            continue
        labels = document.get("metadata", {}).get("labels", {})
        if "scenario" in labels or "tier" in labels:
            labelled_documents.append(labels)
            assert labels.get("scenario") == scenario_name
            assert labels.get("tier") == tier

    assert labelled_documents, f"{path} has no scenario/tier labels"


def test_named_subsets_are_explicit_frozen_catalogue_subsets():
    from config.runtime import (
        EVAL_SCENARIOS_BY_TIER,
        EVALUATION_SUBSET_COUNT,
        FROZEN_SCENARIOS,
        LEADERBOARD_SCENARIOS,
        LEADERBOARD_SUBSET_COUNT,
        SCENARIOS_BY_TIER,
        TRAINING_CURRICULUM_SUBSET_COUNT,
    )

    frozen = set(FROZEN_SCENARIOS)
    evaluation = {
        scenario for scenarios in EVAL_SCENARIOS_BY_TIER.values() for scenario in scenarios
    }
    leaderboard = {scenario for scenario, _tier in LEADERBOARD_SCENARIOS}
    curriculum = {scenario for scenarios in SCENARIOS_BY_TIER.values() for scenario in scenarios}

    assert evaluation <= frozen
    assert leaderboard <= frozen
    assert curriculum <= frozen
    assert EVALUATION_SUBSET_COUNT == len(evaluation) == 11
    assert LEADERBOARD_SUBSET_COUNT == len(leaderboard) == 7
    assert TRAINING_CURRICULUM_SUBSET_COUNT == len(curriculum) == 23
    assert len(SCENARIOS_BY_TIER["named_replays"]) == 5
