import hashlib
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SPEC = importlib.util.spec_from_file_location(
    "transfer_t4", Path(__file__).parents[1] / "scripts/transfer_t4_model_files.py"
)
transfer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(transfer)


def test_exact_pinned_metadata():
    evidence = Path(__file__).parents[1] / "artifacts/evidence/stage7"
    weights = json.loads((evidence / "qwen_a09a354_weight_metadata_v1.json").read_text())
    tokens = json.loads((evidence / "tokenizer_files_a09a354_v1.json").read_text())
    expected = {**weights["shards"], **tokens["files"]}
    assert len(transfer.FILES) == 12
    for name, value in expected.items():
        assert transfer.FILES[name] == (value["size_bytes"], value["sha256"])
    assert sum(size for size, _ in transfer.FILES.values()) == 15242799511


@pytest.mark.parametrize("url", [
    "http://huggingface.co/file", "https://huggingface.co.attacker.invalid/file",
    "https://attacker.invalid/file", "https://user:secret@huggingface.co/file",
    "https://huggingface.co:444/file", "https://cdn.hf.co.attacker.invalid/file",
    "file:///content/file", "https://huggingface.co/file#fragment",
])
def test_unsafe_hosts_rejected(url):
    with pytest.raises(transfer.TransferError):
        transfer.checked_url(url)


def test_redirect_queries_not_recorded():
    result = transfer.source_record("https://us.aws.cdn.hf.co/path?secret=do-not-record")
    assert result["query_present"]
    assert "do-not-record" not in json.dumps(result)


def test_existing_report_preserved(tmp_path):
    path = tmp_path / "report"
    transfer.write_new(path, b"old")
    with pytest.raises(FileExistsError):
        transfer.write_new(path, b"new")
    assert path.read_bytes() == b"old"


def test_bounds_fail_closed(tmp_path, monkeypatch):
    state = {"network_payload_bytes": 0, "minimum_observed_free_bytes": 50 * transfer.GIB}
    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda _: SimpleNamespace(free=19 * transfer.GIB))
    with pytest.raises(transfer.TransferError, match="Free disk"):
        transfer.check_bounds(tmp_path, state, float("inf"))
    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda _: SimpleNamespace(free=50 * transfer.GIB))
    state["network_payload_bytes"] = transfer.NETWORK_LIMIT + 1
    with pytest.raises(transfer.TransferError, match="network"):
        transfer.check_bounds(tmp_path, state, float("inf"))
    state["network_payload_bytes"] = 0
    with pytest.raises(transfer.TransferError, match="budget"):
        transfer.check_bounds(tmp_path, state, 0)
    (tmp_path / "file").write_bytes(b"abc")
    monkeypatch.setattr(transfer, "WORKSPACE_LIMIT", 2)
    with pytest.raises(transfer.TransferError, match="workspace"):
        transfer.check_bounds(tmp_path, state, float("inf"))


def test_non_colab_launch_has_no_side_effects(tmp_path):
    with pytest.raises(transfer.TransferError):
        transfer.execute_transfer(tmp_path / "atlasops-t4-model-transfer-test")
    assert list(tmp_path.iterdir()) == []


class Response:
    status = 200

    def __init__(self, raw, length=None):
        import io
        self.stream = io.BytesIO(raw)
        self.headers = {"Content-Length": str(len(raw) if length is None else length)}

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return "https://us.aws.cdn.hf.co/file?temporary-secret"

    def read(self, size):
        return self.stream.read(size)


def setup_stream(tmp_path, monkeypatch, response):
    state = {"network_payload_bytes": 0, "minimum_observed_free_bytes": 50 * transfer.GIB,
             "transfers": []}
    monkeypatch.setattr(transfer.shutil, "disk_usage", lambda _: SimpleNamespace(free=50 * transfer.GIB))
    monkeypatch.setattr(transfer.urllib.request, "build_opener",
                        lambda *args: SimpleNamespace(open=lambda *a, **kw: response))
    # Windows lacks O_NOFOLLOW; the production entry point rejects non-POSIX.
    monkeypatch.setattr(transfer.os, "O_NOFOLLOW", 0, raising=False)
    monkeypatch.setattr(transfer.os, "chmod", lambda *a, **kw: None)
    return state


def test_small_stream_verified_without_extra_copy(tmp_path, monkeypatch):
    raw = b"synthetic bytes"
    state = setup_stream(tmp_path, monkeypatch, Response(raw))
    transfer.stream_file("test.bin", (len(raw), hashlib.sha256(raw).hexdigest()),
                         tmp_path, tmp_path, state, float("inf"))
    assert state["transfers"][0]["status"] == "VERIFIED"
    assert state["network_payload_bytes"] == len(raw)
    assert [p.name for p in tmp_path.iterdir()] == ["test.bin"]
    assert "temporary-secret" not in json.dumps(state)


@pytest.mark.parametrize("raw,length,digest", [
    (b"bad", 3, "0" * 64), (b"ab", 3, "0" * 64),
    (b"abcd", 3, "0" * 64), (b"abc", 9, "0" * 64),
])
def test_failure_preserves_partial_file(tmp_path, monkeypatch, raw, length, digest):
    state = setup_stream(tmp_path, monkeypatch, Response(raw, length))
    with pytest.raises(transfer.TransferError):
        transfer.stream_file("test.bin", (3, digest), tmp_path, tmp_path, state, float("inf"))
    assert (tmp_path / "test.bin").exists()
    assert len(state["transfers"]) == 1
