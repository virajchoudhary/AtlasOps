"""Prepared website-demo protocol, deliberately not the active G4 declaration."""

from __future__ import annotations

import copy
from pathlib import Path

from config.g4_protocol import (
    APPROVED_G4_V38_SOURCE_GUARD_PROFILE,
    REPO_ROOT,
    build_integrated_protocol_profile,
    file_sha256,
    protocol_fingerprint,
)

SOURCE_BASIS_COMMIT = "18e3010b76c7ad4b57a1a525717b469e5b87202f"
HISTORICAL_PROFILE_SHA256 = "6547e83e4f765aaf86e1822c89aa7dba8429489d461bfcf7e3e3e2249c3dba58"
CANDIDATE_MARKER = "G4-WEBSITE-DEMO-OUTPUT-CONTRACT-2026-10-07"
CANDIDATE_VERSION = "g4-website-demo-output-contract-v1"
SOURCE_SHA256 = {
    "agents/prompts/diagnosis.md": "311fda38079228bda1eb8703ba90c66bb2716129935e1a74ed7ca28805224360",
    "agents/coordinator.py": "a74f5ad114a0e71280ad2a967871207459d451c4b579bd29f3db45482855f8c1",
    "bench/integrated_inference.py": "551c5a3e459a59ef1e2a042af38b27c3ebc6f7d6335e0728d8515f860c0839d8",
    "scripts/run_stage4_golden_incident.py": "045943deb9258956a6d297276932e82da004b6d197ce092d0c676e7c29847395",
    "demo/operator.py": "3a953f6f2a9a1e5d29bc8382f06d61d41bed5b9a46cbc9ee042d799fd4b924a7",
    "demo/read_api.py": "42142d118047c37363e1ca3b9bbfa164061ede67ac41a888d84531fa695c8381",
    "demo/incident_monitor.py": "2af848db16da0198520c772754c6ff288988b21301dac2a549a8e513851e8e1b",
    "agents/approval.py": "f8fd1975c2648f20c3c5e1fe113feb8737c9dc16276764900349bd517059902b",
    "agents/policy_remediation.py": "9810651956c24067bd99a16e1487c0157fdb78b35e0e92732e11729f7d4fbc39",
    "training/grpo_environment.py": "8826379511dacda3af33178a5f0152ea6efc7b19eb4332e6a0265e4d92721c2d",
    "agents/verifier.py": "850dc5f2f197887ef81fda1190cbf576f03e79033a1dbe395359ee1bcb4610df",
    "agents/tools/chaos.py": "089c5dec071ecac778ceb3b5c47f4a92cc71bd1dd3c9807dcd4785a3992dc059",
    "agents/grounding.py": "c228e7a4bce97f74f51529db94af0a3a22da23ac92ef89fda52f30f50add9df1",
    "scripts/qualify_integrated_inference.py": "2953facbc97e5043b6e627f56c2343f50bef430145267f76acc948aebe33bbb5",
    "scripts/serve_integrated_inference.py": "ad7851740cbeefb821d0a1f9fd80e2dd16f56100e4671ec4b4e6b2ca7f065e73",
    "bench/integrated_inference_remote.py": "f5cab645a0f4e1da2c0d5f4c4a48e940bb84f4b26f6c8016876d323670b58118",
    "agents/tool_policy.py": "c7c755b1fa33c9b77cc474d92706915d1c6dbf8efa7932ebbd24fcf9c3ff494b",
    "agents/tools/__init__.py": "91a2b7a2248cf4bc43e7e726a939a9fefaf7e7c8b512a969a63dba4bc03e5d4a",
    "config/g4_protocol.py": "00adeaf598beea2de5e164c494febaa8a08a5d0e0aa6844b9fc404bca295433d",
    "config/scenario_catalog.py": "6d94db05f8d2e95622eb02a4363e9321e9fb75de78f91a51c2d8c04d956fefd4",
    "config/runtime.py": "4502a5e27d2a0fdd88ae7842347e6c85d717e71de6c9f7710d42951fadbf4090",
    "bench/chaos_manifests/single_fault/sf-002.yaml": "23aac11f0faed1dd89b3e2428770483fd34b48a693681f4bea0badb757ff04b5",
    "agents/prompts/triage.md": "956a1489758cb0fe9d7567728b6180e357825a2534c219ebb961fc9c51029f1d",
    "agents/prompts/remediation.md": "00d0d3b24ba00007fd2e00d9c99f6c4ae9782cc6b42c495720648ec6c49bf344",
    "agents/prompts/comms.md": "be58db4dcef9b42296ef81217a22ec44842d608b25bc4ee6d29e39b0216f461c",
}
WEBSITE_CONTRACT = {
    "scenario_id": "single_fault/sf-002",
    "kube_context": "kind-atlasops-local",
    "maximum_website_launches": 1,
    "launch_claim": "fixed-local-authority-exclusive-create-never-release",
    "historical_ledger": "independent-nonempty-pinned-inventory-byte-identical-inclusion",
    "poison": "check-preserved-and-execution-roots",
    "approval": "owned-process-exact-action-digest-same-origin-session",
    "restart": "recorded-monitor-only-no-approval-without-owned-process",
    "activity": "authenticated-role-phase-tool-metadata-no-narration",
    "model_output": "plain-direct-action-json-no-native-tool-envelope",
    "action_schema": {
        "tool": "nonempty string",
        "arguments": "JSON object",
        "agent_claimed_resolved": "boolean",
        "actions_list": "rejected",
        "duplicate_keys_or_nonfinite_numbers": "rejected",
    },
    "diagnosis": "text-root-cause-top-level-observed-evidence",
    "outcome": "unchanged-causal-verifier-before-harness-cleanup",
}


def candidate_profile() -> dict:
    """Return an independent draft without changing any historical/active dict."""
    profile = copy.deepcopy(APPROVED_G4_V38_SOURCE_GUARD_PROFILE)
    profile["protocol_marker"] = CANDIDATE_MARKER
    profile["profile_version"] = CANDIDATE_VERSION
    profile["diagnosis_prompt"].update({
        "version": CANDIDATE_VERSION,
        "sha256": SOURCE_SHA256["agents/prompts/diagnosis.md"],
    })
    profile["agent_prompt_sha256"]["diagnosis"] = SOURCE_SHA256["agents/prompts/diagnosis.md"]
    profile["causal_evidence_policy"]["source_sha256"].update(SOURCE_SHA256)
    profile["website_demo"] = copy.deepcopy(WEBSITE_CONTRACT)
    return profile


def observe_candidate_profile(model_identity: dict, metrics_observation: dict) -> dict:
    """Observe the draft shape without installing it as the active protocol."""
    profile = build_integrated_protocol_profile(
        model_identity=model_identity, metrics_observation=metrics_observation,
    )
    profile["protocol_marker"] = CANDIDATE_MARKER
    profile["profile_version"] = CANDIDATE_VERSION
    profile["diagnosis_prompt"]["version"] = CANDIDATE_VERSION
    profile["causal_evidence_policy"]["source_sha256"] = {
        relative: file_sha256(REPO_ROOT / relative)
        for relative in candidate_profile()["causal_evidence_policy"]["source_sha256"]
    }
    profile["website_demo"] = copy.deepcopy(WEBSITE_CONTRACT)
    return profile


def validate_candidate_profile(observed: dict) -> dict:
    if observed != candidate_profile():
        raise ValueError("Observed runtime does not match the prepared website protocol")
    return observed


def inspect_candidate(root: Path = REPO_ROOT) -> dict:
    """Local files only; no model initialization, cluster access or reservation."""
    mismatches = []
    sources = candidate_profile()["causal_evidence_policy"]["source_sha256"]
    for relative, expected in sources.items():
        try:
            actual = file_sha256(root / relative)
        except OSError:
            actual = None
        if actual != expected:
            mismatches.append(relative)
    historical_unchanged = (
        protocol_fingerprint(APPROVED_G4_V38_SOURCE_GUARD_PROFILE) == HISTORICAL_PROFILE_SHA256
    )
    return {
        "status": "PREPARED" if not mismatches and historical_unchanged else "SOURCE_MISMATCH",
        "source_basis_commit": SOURCE_BASIS_COMMIT,
        "candidate_profile_sha256": protocol_fingerprint(candidate_profile()),
        "historical_profile_unchanged": historical_unchanged,
        "source_mismatches": mismatches,
        "active": False,
        "runtime_qualified": False,
        "launch_authorized_by_this_report": False,
        "empirical_pass": False,
    }


if __name__ == "__main__":
    import json

    print(json.dumps(inspect_candidate(), indent=2))
