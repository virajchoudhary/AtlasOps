"""Fetch Qwen2.5-0.5B-Instruct from Docker Hub's OCI model registry and convert it for transformers.

The right-sizing CPU GRPO experiment (docs/project/RIGHTSIZING_GRPO_CPU_V1.md) used this model
because huggingface.co was not reachable from the session that ran it. Every blob is checked
against its manifest digest, and the manifest against the digest recorded for the experiment,
before anything is converted:

    python -m scripts.fetch_qwen_oci_model --download-dir <new dir> --output-dir <new dir>

The output directory holds float32 safetensors, the tokenizer (end of turn ``<|im_end|>``)
and a provenance.json. Neither directory may be inside the repository.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import urllib.request
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
REGISTRY = "registry-1.docker.io"
REPOSITORY = "ai/qwen2.5"
TAG = "0.5B-F16"
EXPERIMENT_MANIFEST_SHA256 = "a78aecb6b2e31b763a969e79b56f1ee4bdf199cd85c908550979e929bced9af7"
LAYER_NAMES = {"application/vnd.docker.ai.gguf.v3": "model.gguf",
               "application/vnd.docker.ai.license": "LICENSE.txt"}


def blob_plan(manifest: dict[str, Any]) -> list[tuple[str, str]]:
    """(digest, file name) for the config and each layer; refuses unknown or repeated layers."""
    plan = [(manifest["config"]["digest"], "config.json")]
    for layer in manifest["layers"]:
        name = LAYER_NAMES.get(layer["mediaType"])
        if name is None:
            raise ValueError(f"Unexpected layer media type {layer['mediaType']}")
        plan.append((layer["digest"], name))
    names = [name for _, name in plan]
    if sorted(names) != sorted(set(names)) or "model.gguf" not in names:
        raise ValueError("Manifest must have exactly one GGUF layer and no repeated layers")
    return plan


def _open(url: str, token: str, accept: str | None = None):
    headers = {"Authorization": f"Bearer {token}"}
    if accept:
        headers["Accept"] = accept
    return urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=600)


def download(directory: Path, manifest_sha256: str) -> dict[str, str]:
    scope = f"repository:{REPOSITORY}:pull"
    with urllib.request.urlopen(f"https://auth.docker.io/token?service=registry.docker.io&scope={scope}",
                                timeout=60) as response:
        token = json.load(response)["token"]
    base = f"https://{REGISTRY}/v2/{REPOSITORY}"
    with _open(f"{base}/manifests/{TAG}", token, "application/vnd.oci.image.manifest.v1+json") as response:
        manifest_bytes = response.read()
    if hashlib.sha256(manifest_bytes).hexdigest() != manifest_sha256:
        raise SystemExit(f"{REPOSITORY}:{TAG} manifest changed since the experiment; refusing to continue")
    directory.mkdir(parents=True, exist_ok=False)
    (directory / "manifest.json").write_bytes(manifest_bytes)
    digests = {}
    for digest, name in blob_plan(json.loads(manifest_bytes)):
        hasher = hashlib.sha256()
        with _open(f"{base}/blobs/{digest}", token) as response, (directory / name).open("wb") as out:
            for block in iter(lambda: response.read(1 << 20), b""):
                hasher.update(block)
                out.write(block)
        if f"sha256:{hasher.hexdigest()}" != digest:
            raise SystemExit(f"{name} does not match its manifest digest")
        digests[name] = digest
    return digests


def convert(download_dir: Path, output: Path, digests: dict[str, str], manifest_sha256: str) -> None:
    import gguf
    import torch
    import transformers
    from transformers import AutoModelForCausalLM, AutoTokenizer

    tokenizer = AutoTokenizer.from_pretrained(download_dir, gguf_file="model.gguf")
    model = AutoModelForCausalLM.from_pretrained(download_dir, gguf_file="model.gguf", dtype=torch.float32)
    model.save_pretrained(output, safe_serialization=True)
    tokenizer.save_pretrained(output)
    # The GGUF conversion ends turns with <|endoftext|>; Qwen2.5-Instruct and its generation config use <|im_end|>.
    tokenizer = AutoTokenizer.from_pretrained(output)
    tokenizer.eos_token = "<|im_end|>"
    tokenizer.save_pretrained(output)

    fields = gguf.GGUFReader(download_dir / "model.gguf").fields
    config = json.loads((download_dir / "config.json").read_text())["config"]
    provenance = {
        "registry": REGISTRY, "repository": REPOSITORY, "tag": TAG, "manifest_sha256": manifest_sha256,
        "gguf_blob": digests["model.gguf"], "license_blob": digests["LICENSE.txt"],
        "config_blob": digests["config.json"],
        "gguf_metadata": {key: fields[key].contents() for key in ("general.name", "general.license")}
        | {"parameters": config["parameters"], "quantization": config["quantization"]},
        "conversion": f"transformers {transformers.__version__} GGUF loader (gguf package), "
                      "dequantized F16 -> saved float32 safetensors",
        "why_not_huggingface": "huggingface.co is not reachable from this session's network policy",
        "tokenizer_correction": "eos_token set to <|im_end|> (151645) to match the upstream Qwen2.5-Instruct "
                                "tokenizer_config and this model's generation_config; the GGUF conversion had "
                                "<|endoftext|>. The pre-tokenizer split pattern equals Qwen2's (single-digit numbers).",
    }
    (output / "provenance.json").write_text(json.dumps(provenance, indent=2) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--download-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest-sha256", default=EXPERIMENT_MANIFEST_SHA256)
    args = parser.parse_args()
    for path in (args.download_dir, args.output_dir):
        if path.resolve().is_relative_to(REPO):
            parser.error("model files must live outside the repository")
    if args.output_dir.exists():
        parser.error("--output-dir must not exist yet")
    digests = download(args.download_dir, args.manifest_sha256)
    convert(args.download_dir, args.output_dir, digests, args.manifest_sha256)
    print(json.dumps(json.loads((args.output_dir / "provenance.json").read_text()), indent=2))


if __name__ == "__main__":
    main()
