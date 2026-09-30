# G7 Pilot Environment v1

**Status: preparation-only; not a host selection, D1/D2/D3 approval, or
launch authorization.** This record pins a candidate software environment for
one bounded QLoRA SFT pilot. It does not establish GPU, driver, BF16,
bitsandbytes, memory-fit, training, adapter-reload, empirical, or gate
readiness. No model weights were downloaded and no image was built.

## Frozen Environment Candidate

- Target: Linux x86_64, CPython 3.12.11, glibc 2.28 or later.
- OCI base: `python:3.12.11-slim-bookworm`, Linux/amd64 child manifest
  `sha256:c00fc7b44d844b6da22861ec24af43968a5200eac4ec607b4725d585165d6b49`.
  Docker Hub's multi-platform index digest at resolution was
  `sha256:519591d6871b7bc437060736b9f7456b8731f1499a57e22e6c285135ae657bf7`.
  The Dockerfile uses the architecture-specific child digest, not a mutable tag.
- Direct training stack:
  `torch==2.7.1`, `transformers==4.57.6`, `trl==0.19.1`,
  `peft==0.17.1`, `datasets==4.8.5`, `accelerate==1.14.0`,
  `tokenizers==0.22.2`, `huggingface-hub==0.36.2`,
  `bitsandbytes==0.46.1`.
- Direct AtlasOps runner-import boundary:
  `fastapi==0.141.1`, `pydantic==2.13.4`, `httpx==0.28.1`,
  `requests==2.34.2`, and `jinja2==3.1.6`. `training.sft`'s preparation
  path imports the coordinator and its tool registry; FastAPI/Pydantic are
  imported by the coordinator, HTTPX by the coordinator and retry/metrics
  modules, Requests by the registered tools, and Jinja2 by SFT rendering and
  communications. FastAPI 0.141.1 metadata requires Pydantic >=2.9, which is
  satisfied by the pinned 2.13.4.
- The lock covers the runner's imports, not all project runtime extras.
  `uvicorn` is imported only under the coordinator's `__main__` guard;
  `bench.grpo_eval` and `recommender.hybrid` are local, deferred code paths.
  The registry's Google Cloud Monitoring import is guarded with a fallback.
  `python-dotenv`, the Kubernetes Python client, and YAML APIs are not imported
  by the SFT runner's reachable preparation path. PyYAML remains in this lock
  as a transitive dependency of the pinned model stack; none of these
  exclusions claim that unrelated AtlasOps commands can run in this image.
- `torch==2.7.1` PyPI metadata resolves the Linux x86_64 CUDA runtime to
  CUDA 12.6 packages, including `nvidia-cuda-runtime-cu12==12.6.77`,
  `nvidia-cublas-cu12==12.6.4.1`, `nvidia-cudnn-cu12==9.5.1.17`,
  `nvidia-nccl-cu12==2.26.2`, and `triton==3.3.1`. The full transitive set
  is in the hash-locked file, not just these illustrative runtime pins.
- `bitsandbytes==0.46.1` is selected as the explicit QLoRA/8-bit optimizer
  dependency. Its upstream release added a CUDA 12.9 build and its versioned
  README lists Linux x86-64 NVIDIA support with SM75+ recommended. This is
  package-source compatibility evidence, not proof that this exact stack loads
  or trains on a particular host.
- The generated lock contains 72 pinned packages and 1,915 SHA-256 hashes. Its
  SHA-256 is
  `b649bfa91f1232b9b0fbf247927516a281c8769a6556330d561c7a9bd6992d9b`.
- Lock input: [`sft-pilot-v1.in`](../../requirements/sft-pilot-v1.in).
  It preserves all eight direct pins in
  [`train-constraints.txt`](../../requirements/train-constraints.txt), adds
  the explicit `bitsandbytes` pin, and pins the five imported runner-boundary
  packages using versions from the existing `dev-win-py312.lock`. It does not
  install AtlasOps editable, development tools, vLLM, GRPO, evaluation, or
  tracking extras.
- Recipe: [`Dockerfile`](../../infra/training/sft-pilot/Dockerfile). Build
  context is the repository root. It installs only the complete hash lock from
  PyPI using wheels and runs `pip check`; it does not copy project data or
  credentials. Its default `CMD` is a no-op and never starts SFT. At runtime,
  the reviewed source checkout must be mounted separately and bound to its
  exact full Git SHA.

## Hardware and Storage Recommendation

For a later, separately approved university-hosted run, prefer one
NVIDIA A100 80 GB with BF16 support. One NVIDIA L40S 48 GB is a conditional
alternative only if the exact approved configuration and sequence length pass
host-specific memory/readiness checks with adequate headroom. These are
recommendations, not available or reserved resources, and no utilization
claim has been measured. See NVIDIA's [A100](https://www.nvidia.com/en-us/data-center/a100/)
and [L40S](https://www.nvidia.com/en-us/data-center/l40s/) specifications.

Use a 50 GiB initial scratch target and a 100 GiB hard per-run ceiling, with at
least 20 GiB left free. Before any separately authorized model transfer,
calculate actual base-file, package-cache, dataset, checkpoint, log, and
temporary-file requirements. If the verified estimate exceeds 80 GiB, stop
and obtain a new explicit storage decision; do not silently raise the cap.
This is a planning limit, not a claim that a particular host has that space.

The [source-bound tokenizer preflight v3 report](../../artifacts/evidence/stage7/sft_tokenizer_preflight_v3.json)
(`sha256:4833517a7e0f9b78615c76af9aafca8f7127f83696cea2befd43b0ca1e42eda7`)
reports `PASS` for 68 rows, maximum input length 5,738 tokens, zero
truncation, and a passing assistant-mask contract at `max_seq_length=8192`.
It also records that held-out outcomes were not accessed and that no weights,
inference, or training were started. This is tokenizer/mask evidence only. The
tokenizer's 32,768 position limit is not a training sequence-length
recommendation or a GPU memory estimate. Do not rely on `training.sft`'s
`2048` default implicitly.
`config/sft_pilot_v2.json` must bind the effective `max_sequence_length=8192`
to that exact preflight evidence and the D3 preparation decision. No GPU
memory-fit or execution readiness was measured.

## Reproduction and Provenance

The lock was resolved with `uv 0.12.5` from PyPI metadata for Python 3.12.11
and `x86_64-manylinux_2_28`, using only binary candidates and generated hashes:

```powershell
uv pip compile requirements/sft-pilot-v1.in `
  --output-file requirements/sft-pilot-linux-py312.lock `
  --python-version 3.12.11 `
  --python-platform x86_64-manylinux_2_28 `
  --only-binary :all: `
  --generate-hashes `
  --no-cache `
  --default-index https://pypi.org/simple
```

The 72-package resolution did not install or download package wheels,
PyTorch/CUDA wheels, or model weights. The previous first-generation
resolution downloaded `uv`'s 20.7 MiB managed CPython 3.12.11 resolver
runtime; the amended resolution reused it. No GPU, model-weight, host,
cluster, or paid-resource check was performed.

Source-level API inspection found that TRL v0.19.1 declares
`SFTConfig.assistant_only_loss`, accepts `processing_class` in `SFTTrainer`,
and requests `return_assistant_tokens_mask=True` for conversational datasets,
then preserves `assistant_masks` for the loss collator. Its package metadata
requires `transformers>=4.51.0`, which admits the pinned `4.57.6`. This
confirms the parameter/API contract from the pinned source and metadata only;
the Python environment was not installed or imported, and no GPU training was
run.

For an authorized future image build, the Dockerfile's installation command
uses `pip --isolated --only-binary=:all: --require-hashes` and an explicit
PyPI index, followed by `pip check`. Image building was not performed here:
the CUDA runtime wheels are multi-gigabyte. Record the resulting image digest
and both the lock and Dockerfile SHA-256 values in
`config/sft_pilot_v2.json` before any launch-gate review.

The parent project contract remains authoritative. The existing
[`G7 SFT Pilot Acceptance v1`](G7_SFT_PILOT_ACCEPTANCE_V1.md) still requires
explicit decisions, exact corpus/model/tokenizer/source provenance, runtime
validation on the approved NVIDIA host, a completed-run inventory, and an
independent adapter reload. This environment record closes none of those
gates.

## Primary Sources

- [Existing SFT dependency constraints](../../requirements/train-constraints.txt)
- [PyPI metadata for PyTorch 2.7.1](https://pypi.org/pypi/torch/2.7.1/json)
- [FastAPI 0.141.1 PyPI metadata](https://pypi.org/pypi/fastapi/0.141.1/json)
- [Pydantic 2.13.4 PyPI metadata](https://pypi.org/pypi/pydantic/2.13.4/json)
- [bitsandbytes 0.46.1 release notes](https://github.com/bitsandbytes-foundation/bitsandbytes/releases/tag/0.46.1)
- [bitsandbytes 0.46.1 README](https://github.com/bitsandbytes-foundation/bitsandbytes/blob/0.46.1/README.md)
- [TRL 0.19.1 SFTConfig](https://github.com/huggingface/trl/blob/v0.19.1/trl/trainer/sft_config.py)
- [TRL 0.19.1 SFTTrainer](https://github.com/huggingface/trl/blob/v0.19.1/trl/trainer/sft_trainer.py)
- [TRL 0.19.1 dependency metadata](https://github.com/huggingface/trl/blob/v0.19.1/setup.cfg)
- [Official Python image](https://hub.docker.com/_/python)
- [NVIDIA A100 specifications](https://www.nvidia.com/en-us/data-center/a100/)
- [NVIDIA L40S specifications](https://www.nvidia.com/en-us/data-center/l40s/)
