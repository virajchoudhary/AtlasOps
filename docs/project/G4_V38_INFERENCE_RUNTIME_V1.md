# G4 v3.8 Inference Runtime v1

This prospective packaging amendment derives from the unchanged historical
72-package SFT lock, SHA-256
`b649bfa91f1232b9b0fbf247927516a281c8769a6556330d561c7a9bd6992d9b`.
It does not relabel that environment or modify any SFT artifact or evidence.

Use Python 3.12.11 and install both locks together with hashes enforced:

```sh
python -m pip install --require-hashes --only-binary=:all: \
  -r requirements/sft-pilot-linux-py312.lock \
  -r requirements/g4-v38-inference-runtime-v1.lock
python -m pip check
python -B -m scripts.qualify_inference_runtime
```

The overlay adds only `uvicorn==0.52.1` and its required missing dependency
`click==8.4.2`, using the repository's established pins. `h11==0.16.0`
is repeated with identical hashes because it is already in the base lock.
No existing package version or distribution hash is upgraded or substituted.
`config/g4_v38_inference_runtime_v1.json` pins both lock digests.

Serve using `python -B -m scripts.serve_g4_v38_inference_v1` with the
same arguments as `scripts.serve_integrated_inference`. This wrapper verifies
the exact package runtime before invoking the unchanged v3.8 server.
Runtime startup qualification performs only a bounded loopback health check;
it does not load a model, reserve an incident, or establish CUDA/T4 readiness.

Qwen/tokenizer revision, v17 adapter, PEFT, bitsandbytes, PyTorch, decoding,
inference deadlines, authentication, credential isolation, local operational
authority, exact P1 permits, and objective verification remain unchanged.
The original v3.8 protocol/source profile remains unchanged.

After green CI, independent review, and merge, restore the combined locks in a
fresh private Kaggle environment. Then require actual CUDA/T4 readiness and
the existing benign bounded model transport qualification. Only a successful
qualification permits the already-authorized attempt 016 once. Preserve
qualification failure and stop without package, model, hardware, or timeout
changes. G4/G8 remain unpassed; no GRPO, tuning, Test, or certification follows
from this packaging amendment.
