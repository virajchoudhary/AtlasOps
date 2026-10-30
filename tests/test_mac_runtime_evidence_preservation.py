import subprocess
from pathlib import Path

import pytest

from scripts.package_submission import collect_submission_assets

ROOT = Path(__file__).resolve().parents[1]
EVIDENCE = "artifacts/evidence/atlasops-mac-runtime-v1"
ORIGINAL_BLOBS = {
    "CLUSTER_CPU_STARVATION.md": "bc5eb0c0a21cc8bdcd05a02c19d2e124bdffe21f",
    "CONTROLLED_FAULT_RUN.md": "9ad6a6f80e8c802c4d2e9083f3ee158e89bf79a2",
    "CRITICAL_SEVERITY_APPROVAL_GATE.md": "bbd5f972cf2b2fc3009662958ce6d57de198b19f",
    "GRAFANA_ENABLED.txt": "a17973ff8356f900c62d68337595e163e813d062",
    "KAGGLE_NGROK_BLOCKER.md": "3004723118a0b2aaa3c53132e97764a746738e18",
    "RESTART_PERSISTENCE.md": "d0b44728cced4d8bd471436742d610ea554b7b6f",
    "T1_UNIT_TESTS.txt": "ccea75f7dec689d989c8fe5e147e004b2877681d",
    "V4_T3_T9_SUMMARY.md": "bc70cdda23ea1201f4e7c73d1cd9b3c13279652e",
    "alertmanager_alerts.json": "6b321d4bd18fe7d8fe4723de460da6f01f39c371",
    "cluster_pods.txt": "fd5496009b247218819c19ccd700e02137a91c8d",
    "controlled_fault_approval_watch.txt": "fe0b1ecb35895c7108d74757b24117e47a36e5d7",
    "controlled_fault_audit.jsonl": "e3519c46569bf41a3dc607b398c4e1aae882fed8",
    "controlled_fault_coordinator.log": "273eef23a0e2681e732c17fa530f3368820ade64",
    "controlled_fault_manifest.yaml": "3dd440b4c237231bc8cb4d10ffbda776c849e606",
    "controlled_fault_thoughts.json": "c4a83c27ae4c3a0a29574cb4277c21ca4124a693",
    "controlled_fault_timeline.txt": "627a143c28e4a708141be7459e78b2a084a5eb6d",
    "coordinator.log": "f489f0ea780ba108bfc675d05c350197baeccd02",
    "coordinator_audit_log.jsonl": "4203976851477ff115cd1f480850a61a623f05f1",
    "critical_severity_approval_pending.json": "1d86bb7bd7fa2d6c3c6b86bab09e44d77b9926d1",
    "critical_severity_approval_watch.txt": "6220b874f9c7baef5b4b73ae4bbcae1627e0db25",
    "critical_severity_audit.jsonl": "d6da9761add97be5cdc042428aa7dde5040ce06e",
    "critical_severity_policy_matrix.txt": "64fef358614d7d955016bd6b84e47fade1dedb45",
    "critical_severity_thoughts.json": "d39183b4a9b853c8a2bcf066aab46ff756864c20",
    "grafana_runtime_override.yaml": "d34024983205e4693ca207d448b5cb19b0a0f1f7",
    "jaeger_services.json": "423f3ac7442c1144fda1952977ed778b6dc88889",
    "monitoring_components.txt": "3f28d13eb9e55587ba0446621e7ec6c3449999e5",
    "prometheus_rules.json": "aa80446426e9297f9e42ea0db7837e146610fdd5",
    "prometheus_targets.json": "98eaa9c6a5ebcf3647bf3dd5abf7576cee3b40e8",
    "v4_T3_T4.txt": "29e299058217f2f668441e7b6ddd7cd603882a3d",
    "v4_T5_rules_all_bugs.json": "c01193eac0971dc18bdfb90733cc2055e678a55b",
    "v4_T6_approval_paths.json": "2fff8b35251d7e35ff7c3fb915538ae74f4bee61",
    "v4_T7_llm_all_bugs.json": "f0c0335fc7ecb6ea45bae662cad03746bb9112b3",
    "v4_T8_model_outage.json": "64c9e1013b71d1c495dc3a164a6354bb2e08c23d",
    "v4_T9_endpoint_rotation.json": "23f074d1d0e99ce170f3855b1c04f121d4786c7c",
    "v4_driver_t9_isolated.py": "e01ca311beb90ec933060e8d4ea7f8b30d25f665",
}
ORIGINAL_REPORT = "docs/project/ATLASOPS_MAC_RUNTIME_ACCEPTANCE_V1.md"
ORIGINAL_REPORT_BLOB = "73ffb084248d0af6a411bed5d679a038c3201ab0"


@pytest.mark.parametrize(
    ("path", "expected_blob"),
    [
        *((f"{EVIDENCE}/{name}", blob) for name, blob in ORIGINAL_BLOBS.items()),
        (ORIGINAL_REPORT, ORIGINAL_REPORT_BLOB),
    ],
)
def test_original_mac_evidence_keeps_its_git_clean_filter_identity(path, expected_blob):
    result = subprocess.run(
        ["git", "hash-object", f"--path={path}", str(ROOT / path)],
        cwd=ROOT, capture_output=True, text=True, check=True,
    )
    assert result.stdout.strip() == expected_blob


def test_selected_review_inventory_includes_mac_evidence_and_current_entrypoint():
    assets = collect_submission_assets()
    required = {
        *(f"{EVIDENCE}/{name}" for name in ORIGINAL_BLOBS),
        ORIGINAL_REPORT,
        "docs/project/ATLASOPS_MAC_RUNTIME_RECONCILIATION_V1.md",
        "Dockerfile.buglab",
        "docker/buglab-entrypoint.sh",
        ".github/workflows/buglab-runtime.yml",
        "infra/kubernetes/atlasops-prometheus-rules.yaml",
        "infra/local/setup_local.sh",
    }
    assert required <= assets.keys(), sorted(required - assets.keys())
