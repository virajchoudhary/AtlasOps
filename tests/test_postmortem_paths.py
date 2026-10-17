import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from agents.tools import comms


@pytest.fixture
def postmortem_root(tmp_path, monkeypatch):
    root = tmp_path / "postmortems"
    monkeypatch.setenv("POSTMORTEM_DIR", str(root))
    monkeypatch.setattr(comms, "POSTMORTEM_DIR", root)
    return root


def _incident(title="Test incident"):
    return {"title": title, "summary": "Fixture summary"}


def test_relative_basename_is_written_inside_configured_root(
    postmortem_root, tmp_path, monkeypatch
):
    postmortem_root.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)

    result = comms.postmortem_draft(_incident(), "valid.md")

    destination = postmortem_root / "valid.md"
    assert result["success"] is True
    assert result["path"] == str(destination)
    assert result["postmortem_path"] == str(destination)
    assert destination.read_bytes().decode("utf-8").startswith("# Postmortem: Test incident")
    assert not (caller / "valid.md").exists()


def test_explicit_traversal_is_rejected_without_writing_outside_root(
    postmortem_root, tmp_path, monkeypatch
):
    postmortem_root.mkdir()
    caller = tmp_path / "caller"
    caller.mkdir()
    monkeypatch.chdir(caller)
    escaped = tmp_path / "escaped.md"

    result = comms.postmortem_draft(_incident(), "../escaped.md")

    assert result["success"] is False
    assert result["error"]
    assert not escaped.exists()


def test_absolute_path_outside_root_is_rejected(postmortem_root, tmp_path):
    postmortem_root.mkdir()
    outside = tmp_path / "outside.md"

    result = comms.postmortem_draft(_incident(), str(outside))

    assert result["success"] is False
    assert result["error"]
    assert not outside.exists()


def test_title_traversal_is_slugged_before_selecting_default_path(
    postmortem_root, tmp_path
):
    postmortem_root.mkdir()
    date_prefix = datetime.now(UTC).date().isoformat()
    (postmortem_root / f"{date_prefix}-x").mkdir()
    escaped = tmp_path / "escaped.md"

    result = comms.postmortem_draft(_incident("x/../../escaped"))

    assert result["success"] is True
    destination = Path(result["path"])
    assert destination.parent == postmortem_root
    assert destination.suffix == ".md"
    assert destination.name.isascii()
    assert "/" not in destination.name
    assert "\\" not in destination.name
    assert escaped.exists() is False


def test_existing_postmortem_is_never_overwritten(postmortem_root):
    postmortem_root.mkdir()
    destination = postmortem_root / "existing.md"
    destination.write_bytes(b"preserve these bytes")

    result = comms.postmortem_draft(_incident(), str(destination))

    assert result["success"] is False
    assert result["error"]
    assert destination.read_bytes() == b"preserve these bytes"


def test_leaf_symlink_is_rejected_without_overwriting_its_target(
    postmortem_root, tmp_path
):
    postmortem_root.mkdir()
    target = tmp_path / "symlink-target.md"
    target.write_bytes(b"preserve symlink target")
    link = postmortem_root / "linked.md"
    try:
        link.symlink_to(target)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"symlink creation is unavailable: {exc}")

    result = comms.postmortem_draft(_incident(), str(link))

    assert result["success"] is False
    assert result["error"]
    assert link.is_symlink()
    assert target.read_bytes() == b"preserve symlink target"


def test_redirected_root_is_rejected_when_symlink_creation_is_available(
    postmortem_root, tmp_path, monkeypatch
):
    target_root = tmp_path / "actual-postmortems"
    target_root.mkdir()
    redirected_root = tmp_path / "redirected-postmortems"
    try:
        redirected_root.symlink_to(target_root, target_is_directory=True)
    except (NotImplementedError, OSError) as exc:
        pytest.skip(f"directory symlink creation is unavailable: {exc}")
    monkeypatch.setenv("POSTMORTEM_DIR", str(redirected_root))
    monkeypatch.setattr(comms, "POSTMORTEM_DIR", redirected_root)

    result = comms.postmortem_draft(_incident(), str(redirected_root / "blocked.md"))

    assert result["success"] is False
    assert result["error"]
    assert list(target_root.iterdir()) == []


@pytest.mark.parametrize(
    "unsafe_path",
    [
        "C:drive-relative.md",
        r"\\server\share\outside.md",
        r"\\?\C:\outside.md",
        "draft.md:alternate-stream",
        "CON.md",
    ],
)
def test_windows_path_aliases_and_reserved_names_are_rejected(
    postmortem_root, unsafe_path, tmp_path, monkeypatch
):
    postmortem_root.mkdir()
    before = set(postmortem_root.iterdir())
    unsafe_forms = {value.lower().replace("/", "\\") for value in [
        "C:drive-relative.md",
        r"\\server\share\outside.md",
        r"\\?\C:\outside.md",
        "draft.md:alternate-stream",
        "CON.md",
    ]}
    original_open = Path.open

    def guarded_open(path, *args, **kwargs):
        candidate = os.fspath(path).lower().replace("/", "\\")
        if candidate in unsafe_forms:
            raise AssertionError("unsafe Windows path reached the filesystem")
        absolute_candidate = Path(os.path.abspath(path))
        if not absolute_candidate.is_relative_to(tmp_path):
            raise AssertionError("test prevented an out-of-fixture filesystem write")
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", guarded_open)

    result = comms.postmortem_draft(_incident(), unsafe_path)

    assert result["success"] is False
    assert result["error"]
    assert set(postmortem_root.iterdir()) == before


def test_absolute_path_is_allowed_only_as_a_direct_root_child(postmortem_root):
    postmortem_root.mkdir()
    destination = postmortem_root / "absolute.md"

    result = comms.postmortem_draft(_incident(), str(destination))

    assert result["success"] is True
    assert result["path"] == str(destination)
    assert result["postmortem_path"] == str(destination)
    assert destination.read_bytes().decode("utf-8").startswith("# Postmortem: Test incident")


def test_nonstring_output_path_is_rejected(postmortem_root):
    postmortem_root.mkdir()

    result = comms.postmortem_draft(_incident(), None)

    assert result["success"] is False
    assert result["error"]
    assert list(postmortem_root.iterdir()) == []


def test_repeated_default_drafts_get_distinct_paths(postmortem_root):
    postmortem_root.mkdir()

    first = comms.postmortem_draft(_incident())
    second = comms.postmortem_draft(_incident())

    assert first["success"] is True
    assert second["success"] is True
    assert first["path"] != second["path"]
    assert Path(first["path"]).parent == postmortem_root
    assert Path(second["path"]).parent == postmortem_root
    assert Path(first["path"]).read_bytes()
    assert Path(second["path"]).read_bytes()


@pytest.mark.parametrize("output_path", ["", "named.md"])
def test_default_postmortem_reference_survives_incident_history(
    tmp_path, monkeypatch, output_path,
):
    from agents.incident_history import read_incident_history

    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(comms, "POSTMORTEM_DIR", Path("docs/postmortems"))
    result = comms.postmortem_draft(_incident(), output_path)
    assert result["success"] is True
    assert Path(result["path"]).read_text(encoding="utf-8").startswith("# Postmortem:")

    record = {
        "incident_id": "inc-postmortem-reference",
        "comms": {"final": {
            "summary": "Fixture summary",
            "postmortem_path": result["postmortem_path"],
        }},
    }
    history_dir = tmp_path / "history"
    history_dir.mkdir()
    (history_dir / "inc-postmortem-reference.json").write_text(
        json.dumps(record), encoding="utf-8",
    )
    projected = read_incident_history("inc-postmortem-reference", directory=history_dir)
    expected = f"docs/postmortems/{Path(result['path']).name}"
    assert projected["comms"].get("postmortem_path") == expected
    assert result["path"] == expected
