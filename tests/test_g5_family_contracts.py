from bench import scenario_contract as contract


def _catalog_entry(
    scenario_id: str,
    *,
    signatures=None,
    source=None,
    alert=None,
    targets=None,
):
    return {
        "alert_semantic_hash": alert or f"alert-{scenario_id}",
        "fault_signatures": signatures or [f"signature-{scenario_id}"],
        "injected_fault_services": targets or [],
        "red_herring_review_status": "NO_CANDIDATES",
        "scenario_id": scenario_id,
        "source_incident_id": source,
    }


def _catalog(*entries):
    return {"catalog_sha256": "unit", "entries": list(entries)}


def test_single_target_relations_are_family_protected():
    catalog = _catalog(
        _catalog_entry("train-a", targets=["redis-cart"]),
        _catalog_entry("heldout-b", targets=["redis-cart"]),
        _catalog_entry("unrelated-c", targets=["emailservice"]),
        _catalog_entry("network-d", targets=["default->default"]),
    )

    relations = contract.scenario_relationships(catalog)

    assert relations == [{
        "key": "redis-cart",
        "reason": "single_target",
        "scenario_ids": ["heldout-b", "train-a"],
    }]


def test_cross_split_single_target_boundary_fails_closed():
    catalog = _catalog(
        _catalog_entry("train-a", targets=["cartservice"]),
        _catalog_entry("heldout-b", targets=["cartservice"]),
    )
    split = {"splits": {
        "train": ["train-a"],
        "validation": ["heldout-b"],
        "final_test": [],
    }}

    conflicts = contract.cross_split_family_conflicts(split, catalog)

    assert conflicts[0]["reason"] == "single_target"
    assert conflicts[0]["roles"] == ["train", "validation"]
