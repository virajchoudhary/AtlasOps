"""Exact, reviewed settling-only compatibility for the preparation-approved corpus."""

import ast
import subprocess

from training.sft_candidate import (
    candidate_manifest,
    validate_candidate_snapshot,
    validate_source_revision,
)
from training.sft_provenance import REPO_ROOT, canonical_json_sha256

OLD_COORDINATOR = "84832e3549b718705f4491ce6f10a9b0ee4eaf44f5600d722ddf854f8cb6fc28"
NEW_COORDINATOR = "5d7be471592526fbf519fba571c2a1d2ccfd1d976f027736d00f2b3b39aadd0c"


def validate_pilot_candidate(snapshot, manifest):
    """No generic drift tolerance: only one pinned pair with identical non-settling AST."""
    from training.build_sft_candidate import build_candidate_rows

    rows = list(snapshot.rows)
    expected = candidate_manifest(rows, snapshot.raw_bytes, manifest.get("source_git_sha", ""))
    current = expected["source_file_sha256_canonical_lf"]["agents/coordinator.py"]
    frozen = manifest.get("source_file_sha256_canonical_lf", {}).get("agents/coordinator.py")
    if (frozen, current) != (OLD_COORDINATOR, NEW_COORDINATOR):
        return validate_candidate_snapshot(snapshot, manifest)
    previous = subprocess.run(
        ["git", "show", f'{manifest["source_git_sha"]}:agents/coordinator.py'],
        cwd=REPO_ROOT, check=True, capture_output=True,
    ).stdout
    updated = (REPO_ROOT / "agents/coordinator.py").read_bytes()

    def without_settling(raw):
        tree = ast.parse(raw.decode("utf-8"))
        tree.body = [
            node for node in tree.body if getattr(node, "name", None) != "settle_environment"
        ]
        return ast.dump(tree, include_attributes=False)

    if without_settling(previous) != without_settling(updated):
        raise ValueError("Reviewed settling-only compatibility proof failed")
    expected["source_file_sha256_canonical_lf"]["agents/coordinator.py"] = frozen
    if expected != manifest:
        raise ValueError("Pilot candidate manifest/provenance mismatch")
    validate_source_revision(manifest)
    if canonical_json_sha256(build_candidate_rows()) != manifest["rows_semantic_sha256"]:
        raise ValueError("Pilot candidate row replay mismatch")
    return expected
