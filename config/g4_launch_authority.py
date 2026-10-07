"""Fixed local single-launch authority shared by the website and candidate runner."""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path

LOCAL_OPERATOR_AUTHORITY = Path(r"C:\AtlasOps\.codex-tmp\website-launch-claims")


def validate_candidate_launch(fingerprint: str) -> dict:
    from demo.incident_monitor import _object

    record, _ = _object(LOCAL_OPERATOR_AUTHORITY, f"{fingerprint}.json", 16384)
    channel = os.environ.get("ATLASOPS_STAGE4_OPERATOR_CHANNEL_FILE", "")
    token = os.environ.get("ATLASOPS_STAGE4_LAUNCH_TOKEN", "")
    if (
        not record or not token or not Path(channel).is_absolute()
        or record.get("experiment_id") != os.environ.get("STAGE4_EXPERIMENT_ID")
        or record.get("source_sha") != os.environ.get("STAGE4_APPROVED_MAIN_SHA")
        or record.get("protocol_fingerprint") != fingerprint
        or record.get("channel_path") != channel
        or not hmac.compare_digest(
            str(record.get("launch_token_sha256", "")),
            hashlib.sha256(token.encode()).hexdigest(),
        )
    ):
        raise RuntimeError("Candidate launch requires the exact website-owned authority claim")
    return record


def consume_candidate_launch(fingerprint: str) -> None:
    """Exclusive persistent consumption; never released after any outcome."""
    record = validate_candidate_launch(fingerprint)
    from demo.incident_monitor import _safe_file

    relative = f"{fingerprint}.started.json"
    _safe_file(LOCAL_OPERATOR_AUTHORITY, relative, 16384)
    with (LOCAL_OPERATOR_AUTHORITY / relative).open("x", encoding="utf-8") as stream:
        json.dump({
            "experiment_id": record["experiment_id"],
            "source_sha": record["source_sha"],
            "protocol_fingerprint": fingerprint,
            "runner_pid": os.getpid(),
        }, stream)
        stream.flush()
        os.fsync(stream.fileno())
