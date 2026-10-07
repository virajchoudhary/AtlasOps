"""Explicit prospective selection; the historical G4 declaration stays unchanged."""

from __future__ import annotations

import os

from config.g4_protocol import (
    APPROVED_G4_PROTOCOL_PROFILE,
    build_integrated_protocol_profile,
    protocol_fingerprint,
    validate_runtime_protocol_profile,
)

HISTORICAL = "historical"
WEBSITE_CANDIDATE = "website-demo-candidate"


def declared_profile(selection: str = HISTORICAL) -> dict:
    if selection == HISTORICAL:
        return APPROVED_G4_PROTOCOL_PROFILE
    if selection == WEBSITE_CANDIDATE:
        from config.g4_demo_candidate import candidate_profile

        return candidate_profile()
    raise ValueError("Unknown Stage 4 protocol selection")


def selected_profile() -> dict:
    selection = os.environ.get("ATLASOPS_STAGE4_PROTOCOL", HISTORICAL)
    profile = declared_profile(selection)
    if selection != HISTORICAL:
        if os.environ.get("STAGE4_APPROVED_PROTOCOL_SHA256") != protocol_fingerprint(profile):
            raise RuntimeError("Website protocol requires an exact operator-supplied fingerprint")
        if not os.environ.get("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE"):
            raise RuntimeError("Website protocol requires the governed operator channel")
        from config.g4_demo_candidate import inspect_candidate

        if inspect_candidate()["status"] != "PREPARED":
            raise RuntimeError("Website protocol source does not match its reviewed declaration")
        from config.g4_launch_authority import validate_candidate_launch

        validate_candidate_launch(protocol_fingerprint(profile))
    return profile


def observe_selected_profile(model_identity: dict, metrics_observation: dict) -> dict:
    selection = os.environ.get("ATLASOPS_STAGE4_PROTOCOL", HISTORICAL)
    selected_profile()
    if selection == HISTORICAL:
        return validate_runtime_protocol_profile(build_integrated_protocol_profile(
            model_identity=model_identity, metrics_observation=metrics_observation,
        ))
    from config.g4_demo_candidate import observe_candidate_profile, validate_candidate_profile

    return validate_candidate_profile(observe_candidate_profile(model_identity, metrics_observation))
