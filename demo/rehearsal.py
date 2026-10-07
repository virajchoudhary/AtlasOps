"""Scripted presentation fixtures, not agent inference or experimental evidence."""

from __future__ import annotations

import ast
import hashlib

from agents.tool_policy import ROLE_ALLOWED_TOOLS
from config.scenario_catalog import SCENARIO_CATALOG
from ui_read_model import ROOT


def _p1_approval_mode() -> str:
    # Read the policy literal without initializing the operational approval gate.
    tree = ast.parse((ROOT / "agents/approval.py").read_text(encoding="utf-8"))
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "APPROVAL_POLICY"
            for target in node.targets
        ):
            policy = ast.literal_eval(node.value)
            if isinstance(policy, dict) and policy.get("P1") == "approve":
                return policy["P1"]
            break
    raise ValueError("P1 approval policy unavailable")


def rehearsal_catalog() -> dict:
    approval_mode = _p1_approval_mode()
    fixtures = (
        ("single_fault/sf-002", "Payment CPU saturation", "sf-002-paymentservice-cpu",
         "CPU utilization 93%; payment requests timing out.", "CPU utilization 24%; requests healthy."),
        ("single_fault/sf-004", "Frontend packet loss", "sf-004-frontend-network-loss",
         "Ingress packet loss 50%; HTTP 5xx elevated.", "Packet loss 0%; HTTP responses healthy."),
    )
    scenarios = []
    for scenario_id, title, experiment, unhealthy, healthy in fixtures:
        metadata = SCENARIO_CATALOG[scenario_id]
        raw = (ROOT / metadata.manifest_relpath).read_bytes()
        digest = hashlib.sha256(raw.replace(b"\r\n", b"\n")).hexdigest()
        if digest != metadata.manifest_sha256:
            raise ValueError("Rehearsal manifest does not match the frozen catalog")
        tool = "chaos_stop_experiment"
        if tool not in ROLE_ALLOWED_TOOLS["remediation"]:
            raise ValueError("Rehearsal proposal is outside the remediation role ACL")
        scenarios.append({
            "id": scenario_id,
            "title": title,
            "service": metadata.target_services[0],
            "alert": metadata.expected_alert,
            "severity": "P1",
            "approval_mode": approval_mode,
            "diagnosis": metadata.expected_root_cause,
            "unhealthy_observation": unhealthy,
            "healthy_observation": healthy,
            "proposal": {
                "tool": tool,
                "arguments": {"kind": metadata.chaos_kinds[0], "name": experiment,
                              "namespace": "chaos-mesh"},
            },
            "source": metadata.manifest_relpath,
            "sha256": digest,
            "checkout_sha256": hashlib.sha256(raw).hexdigest(),
        })
    return {
        "classification": "SYNTHETIC / NON-LIVE / NON-EMPIRICAL",
        "agent_output": "Scripted fixtures, not model inference",
        "scenarios": scenarios,
    }
