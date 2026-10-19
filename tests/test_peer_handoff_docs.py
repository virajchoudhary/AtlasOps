from pathlib import Path

from scripts.check_submission_links import CURRENT_MARKDOWN_PATHS, check_current_markdown_links
from scripts.package_submission import REQUIRED_REVIEW_ASSETS, collect_submission_assets


ROOT = Path(__file__).resolve().parents[1]
HANDOFF_DOCS = (
    "docs/project/PEER_HANDOFF.md",
    "docs/project/PEER_ONBOARDING.md",
    "docs/project/G13_OFFLINE_SAFEGUARDS_HANDOFF.md",
)


def test_peer_handoff_is_in_required_selected_review_inventory(monkeypatch):
    monkeypatch.chdir(ROOT)
    assert set(HANDOFF_DOCS) <= set(REQUIRED_REVIEW_ASSETS)
    assert set(HANDOFF_DOCS) <= collect_submission_assets().keys()
    assert set(HANDOFF_DOCS) <= set(CURRENT_MARKDOWN_PATHS)


def test_peer_handoff_links_resolve_in_matching_source():
    assert not check_current_markdown_links(ROOT, documents=HANDOFF_DOCS)
