"""Stage only public, immutable Qwen tokenizer metadata; never model weights."""

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path

REPOSITORY = "Qwen/Qwen2.5-7B-Instruct"
REVISION = "a09a35458c702b33eeacc393d103063234e8bc28"
FILES = (
    "tokenizer.json", "tokenizer_config.json", "vocab.json", "merges.txt",
    "config.json", "generation_config.json", "LICENSE",
)
MAX_FILE_BYTES = 16 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024


def stage(destination: Path) -> dict:
    from training.sft_provenance import has_redirecting_path_component

    if destination.exists() or has_redirecting_path_component(destination):
        raise ValueError("Tokenizer destination must be new and redirect-free")
    url = f"https://huggingface.co/api/models/{REPOSITORY}/revision/{REVISION}"
    with urllib.request.urlopen(url, timeout=60) as response:
        metadata = json.loads(response.read(1024 * 1024))
    if metadata.get("sha") != REVISION or metadata.get("id") != REPOSITORY:
        raise ValueError("Immutable model metadata mismatch")
    payloads = {}
    total = 0
    for name in FILES:
        url = f"https://huggingface.co/{REPOSITORY}/resolve/{REVISION}/{name}"
        with urllib.request.urlopen(url, timeout=60) as response:
            raw = response.read(MAX_FILE_BYTES + 1)
        if len(raw) > MAX_FILE_BYTES:
            raise ValueError("Tokenizer file exceeds bounded staging size")
        total += len(raw)
        if total > MAX_TOTAL_BYTES:
            raise ValueError("Tokenizer allowlist exceeds total size bound")
        payloads[name] = raw
    manifest = {
        "schema_version": "atlasops-tokenizer-files-v1",
        "repository": REPOSITORY,
        "revision": REVISION,
        "source": f"https://huggingface.co/{REPOSITORY}/tree/{REVISION}",
        "license": metadata.get("cardData", {}).get("license"),
        "model_weights_downloaded": False,
        "total_bytes": total,
        "files": {
            name: {"sha256": hashlib.sha256(raw).hexdigest(), "size_bytes": len(raw)}
            for name, raw in payloads.items()
        },
    }
    destination.mkdir(parents=True, exist_ok=False)
    if has_redirecting_path_component(destination):
        raise ValueError("Tokenizer destination redirected")
    for name, raw in payloads.items():
        with (destination / name).open("xb") as stream:
            stream.write(raw)
    with (destination / "tokenizer_files_manifest.json").open("x", encoding="utf-8", newline="\n") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
    return manifest


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(stage(args.output), sort_keys=True))
