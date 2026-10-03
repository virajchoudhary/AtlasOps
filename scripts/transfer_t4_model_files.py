"""Pinned file transfer only. No model-library imports or model execution."""

import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import stat
import time
from datetime import datetime, timezone
import urllib.parse
import urllib.request

REPOSITORY = "Qwen/Qwen2.5-7B-Instruct"
REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
GIB = 1024**3
NETWORK_LIMIT = 16 * GIB
WORKSPACE_LIMIT = 20 * GIB
START_FREE = 40 * GIB
MIN_FREE = 20 * GIB
SECONDS = 3600
CHUNK = 1024**2
FILES = {
    "model-00001-of-00004.safetensors": (3945441440, "a1333e6293854747c481288ea83b348226af178dd565c49b6f9495ba1966aba7"),
    "model-00002-of-00004.safetensors": (3864726352, "f5d25a2772cb825164a2a2c0fb6d51a87e282abf21e4dd75bc5cfb3cd0ea6185"),
    "model-00003-of-00004.safetensors": (3864726424, "8efdec4c1bc12317ae1a38dc42b595ce777738a64deea3fcb8a0a91381bcdfd5"),
    "model-00004-of-00004.safetensors": (3556377672, "1a72d403cdf0c1ec3cb7f289f17b394a01e64394c2e9b3c0f94dbce3faf879bd"),
    "model.safetensors.index.json": (27752, "624bf7c47cd12468fdc16e38a47cf4f19e0415b859a223ba3c027eed2f0e1028"),
    "LICENSE": (11343, "832dd9e00a68dd83b3c3fb9f5588dad7dcf337a0db50f7d9483f310cd292e92e"),
    "config.json": (663, "7463bb0ea78315365e6c6b74de4e73bbcc8359dfb0c5a737584e077d42c0b03c"),
    "generation_config.json": (243, "3a8f9087e486054c8a4a08dae2e5a3ba62e23da212b5b8c08bc42cb983c3459f"),
    "merges.txt": (1671839, "599bab54075088774b1733fde865d5bd747cbcc7a547c5bc12610e874e26f5e3"),
    "tokenizer.json": (7031645, "c0382117ea329cdf097041132f6d735924b697924d6f6fc3945713e96ce87539"),
    "tokenizer_config.json": (7305, "5b5d4f65d0acd3b2d56a35b56d374a36cbc1c8fa5cf3b3febbbfabf22f359583"),
    "vocab.json": (2776833, "ca10d7e9fb3ed18575dd1e277a2579c16d108e32f27439684afa0e10b1440910"),
}


class TransferError(RuntimeError):
    pass


def utc():
    return datetime.now(timezone.utc).isoformat()


def raw_json(value):
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode("ascii")


def write_new(path, raw):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(fd, "wb") as stream:
        stream.write(raw)


def checked_url(url):
    parts = urllib.parse.urlsplit(url)
    if (
        parts.scheme != "https" or parts.username or parts.password
        or parts.port not in (None, 443) or parts.fragment
        or not parts.hostname
        or not (
            parts.hostname == "huggingface.co"
            or parts.hostname.endswith(".cdn.hf.co")
            or parts.hostname == "cas-bridge.xethub.hf.co"
        )
    ):
        raise TransferError("Transfer URL is outside the approved HTTPS hosts.")
    return parts


def source_record(url):
    parts = checked_url(url)
    # Redirect query strings may contain temporary signed credentials.
    return {"scheme": parts.scheme, "host": parts.hostname, "path": parts.path,
            "query_present": bool(parts.query), "query_recorded": False}


class RestrictedRedirect(urllib.request.HTTPRedirectHandler):
    def __init__(self, records):
        super().__init__()
        self.records = records

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        record = source_record(newurl)
        self.records.append({"status": code, **record})
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def check_bounds(root, state, deadline):
    if time.monotonic() >= deadline:
        raise TransferError("One-hour transfer/inventory budget exhausted.")
    free = shutil.disk_usage(root).free
    state["minimum_observed_free_bytes"] = min(state["minimum_observed_free_bytes"], free)
    if free < MIN_FREE:
        raise TransferError("Free disk fell below 20 GiB.")
    if state["network_payload_bytes"] > NETWORK_LIMIT:
        raise TransferError("16 GiB network payload cap exceeded.")
    total = 0
    for directory, dirs, files in os.walk(root, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            entry = path.lstat()
            if stat.S_ISLNK(entry.st_mode):
                raise TransferError("Link found in the transfer workspace.")
            if name in files:
                if not stat.S_ISREG(entry.st_mode):
                    raise TransferError("Non-regular transfer workspace file.")
                total += entry.st_size
    if total > WORKSPACE_LIMIT:
        raise TransferError("20 GiB transfer workspace cap exceeded.")
    state["workspace_bytes"] = total


def stream_file(name, expected, snapshot, root, state, deadline):
    size, digest = expected
    url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}"
    record = {"filename": name, "source": source_record(url), "redirects": [],
              "status": "STARTED", "received_bytes": 0}
    state["transfers"].append(record)
    opener = urllib.request.build_opener(
        urllib.request.ProxyHandler({}), RestrictedRedirect(record["redirects"])
    )
    request = urllib.request.Request(url, headers={"Accept-Encoding": "identity"})
    hasher = hashlib.sha256()
    path = snapshot / name
    check_bounds(root, state, deadline)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as output:
        with opener.open(request, timeout=30) as response:
            record["final_source"] = source_record(response.geturl())
            if response.status != 200 or response.headers.get("Content-Encoding", "identity") != "identity":
                raise TransferError("Unexpected HTTP status or compressed response.")
            length = response.headers.get("Content-Length")
            if length is None or not length.isdigit() or int(length) != size:
                raise TransferError("Pinned HTTP file length mismatch.")
            while True:
                check_bounds(root, state, deadline)
                remaining_budget = NETWORK_LIMIT - state["network_payload_bytes"]
                if remaining_budget <= 0:
                    raise TransferError("Network payload budget exhausted.")
                chunk = response.read(min(CHUNK, size - record["received_bytes"] + 1, remaining_budget))
                if not chunk:
                    break
                record["received_bytes"] += len(chunk)
                state["network_payload_bytes"] += len(chunk)
                if record["received_bytes"] > size:
                    raise TransferError("File exceeded pinned byte length.")
                output.write(chunk)
                hasher.update(chunk)
            output.flush()
            os.fsync(output.fileno())
    if record["received_bytes"] != size or hasher.hexdigest() != digest:
        raise TransferError("Pinned file size or SHA-256 mismatch.")
    os.chmod(path, 0o400, follow_symlinks=False)
    record.update(status="VERIFIED", sha256=hasher.hexdigest(), mode="0400")
    print(json.dumps({"file": name, "status": "VERIFIED", "bytes": size,
                      "sha256": digest}, sort_keys=True), flush=True)


def inventory(snapshot, root, state, deadline):
    if {p.name for p in snapshot.iterdir()} != set(FILES):
        raise TransferError("Snapshot file-name set differs from the pinned allowlist.")
    result = []
    for name, (size, expected_hash) in FILES.items():
        check_bounds(root, state, deadline)
        path = snapshot / name
        entry = path.lstat()
        if not stat.S_ISREG(entry.st_mode) or entry.st_nlink != 1 or entry.st_size != size:
            raise TransferError("Snapshot needs one regular, single-link file per approved name.")
        if stat.S_IMODE(entry.st_mode) != 0o400:
            raise TransferError("Snapshot file is not mode 0400.")
        hasher = hashlib.sha256()
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(CHUNK), b""):
                check_bounds(root, state, deadline)
                hasher.update(chunk)
        if hasher.hexdigest() != expected_hash:
            raise TransferError("Independent inventory rehash mismatch.")
        result.append({"path": str(path), "filename": name, "size_bytes": size,
                       "sha256": hasher.hexdigest(), "mode": "0400", "regular_file": True,
                       "hard_link_count": entry.st_nlink})
    index = json.loads((snapshot / "model.safetensors.index.json").read_bytes())
    if set(index["weight_map"].values()) != {n for n in FILES if n.endswith(".safetensors")}:
        raise TransferError("Shard index does not reference exactly the four approved shards.")
    if index["metadata"]["total_size"] != 15231233024:
        raise TransferError("Pinned shard index tensor-byte total mismatch.")
    return result, index["metadata"]["total_size"]


def execute_transfer(root):
    root = Path(root)
    content = Path("/content")
    if (
        os.name != "posix" or content.is_symlink() or content.resolve() != content
        or root.parent != content or not re.fullmatch(r"atlasops-t4-model-transfer-[a-zA-Z0-9-]+", root.name)
    ):
        raise TransferError("Requires a fresh direct /content transfer directory on Linux.")
    if shutil.disk_usage(content).free < START_FREE:
        raise TransferError("Starting free disk is below 40 GiB.")
    if sum(size for size, _ in FILES.values()) > NETWORK_LIMIT:
        raise TransferError("Pinned file list exceeds the transfer allowance.")
    root.mkdir(mode=0o700, exist_ok=False)
    deadline = time.monotonic() + SECONDS
    state = {
        "schema_version": "atlasops-t4-pinned-file-transfer-v1", "status": "RUNNING",
        "started_at_utc": utc(), "repository": REPOSITORY, "revision": REVISION,
        "snapshot_dir": str(root / f"models--Qwen--Qwen2.5-7B-Instruct/snapshots/{REVISION}"),
        "helper_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "approved_scope": "Direct human approval: pinned files and hashing only",
        "evidence_scope": "This helper only; no claim about prior notebook activity",
        "limits": {"network_payload_bytes": NETWORK_LIMIT, "workspace_bytes": WORKSPACE_LIMIT,
                   "starting_free_bytes": START_FREE, "minimum_free_bytes": MIN_FREE,
                   "seconds": SECONDS},
        "network_payload_bytes": 0, "transfers": [], "inventory": [],
        "starting_free_bytes": shutil.disk_usage(content).free,
        "minimum_observed_free_bytes": shutil.disk_usage(content).free,
        "model_loaded": False, "inference_performed": False, "training_performed": False,
        "paid_compute_authorized": False, "held_out_outcomes_accessed": False,
        "gate_promoted": False, "execution_allowed": False,
        "independently_enforced_immutable_storage": False,
        "persistent_storage_verified": False, "encrypted_storage_verified": False,
    }
    previous_handler = signal.getsignal(signal.SIGALRM)

    def timeout_handler(signum, frame):
        raise TransferError("One-hour hard alarm reached.")

    try:
        signal.signal(signal.SIGALRM, timeout_handler)
        signal.alarm(SECONDS)
        snapshot = Path(state["snapshot_dir"])
        current = root
        for part in snapshot.relative_to(root).parts:
            current /= part
            current.mkdir(mode=0o700, exist_ok=False)
        # Preserve a durable start record even if the notebook session dies.
        write_new(root / "transfer-start.json", raw_json(state))
        for name, expected in FILES.items():
            stream_file(name, expected, snapshot, root, state, deadline)
        state["inventory"], state["index_tensor_total_bytes"] = inventory(snapshot, root, state, deadline)
        state["total_file_bytes"] = sum(item["size_bytes"] for item in state["inventory"])
        os.chmod(snapshot, 0o500)
        state["snapshot_mode"] = "0500"
        check_bounds(root, state, deadline)
        state["status"] = "COMPLETE"
    except BaseException as exc:
        state["status"] = "FAILED"
        state["failure"] = {"type": type(exc).__name__,
                            "message": "Transfer stopped; partial files preserved; no automatic retry."}
        # Exception strings can contain signed redirect URLs, so do not persist them.
    finally:
        signal.alarm(0)
        signal.signal(signal.SIGALRM, previous_handler)
    state["completed_at_utc"] = utc()
    raw = raw_json(state)
    write_new(root / "transfer-report.json", raw)
    print(json.dumps({"report": str(root / "transfer-report.json"),
                      "report_sha256": hashlib.sha256(raw).hexdigest(),
                      "status": state["status"]}, sort_keys=True), flush=True)
    return state


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", help="Fresh /content/atlasops-t4-model-transfer-<id> path.")
    args = parser.parse_args()
    outcome = execute_transfer(args.root)
    raise SystemExit(0 if outcome["status"] == "COMPLETE" else 1)
