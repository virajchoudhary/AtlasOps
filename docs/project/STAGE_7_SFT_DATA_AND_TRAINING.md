# Stage 7: Generate SFT Data and Train (Gate G7)

**Status: PARTIAL**

The training corpus and SFT software contract are implemented. No completed
Qwen2.5-7B-Instruct training run or usable adapter is currently preserved.

## Frozen Data

- The generator reconstructs `data/sft_corpus_train.jsonl` on demand; this ignored
  file is absent from a clean checkout. It contains 64 synthetic demonstrations
  from the 16 frozen Train scenarios: four agent roles per scenario.
- Validation and Test scenario IDs are excluded.
- Newly generated corpus manifests record
  `data_origin: scenario_derived_synthetic` and `synthetic: true`, plus corpus counts,
  schema, and canonical LF SHA-256.
- Generated manifests and training configs are written beside the generated corpus.
  This leaves the frozen, tracked `artifacts/evidence/stage7/` records byte-for-byte
  unchanged.
- Corpus and sidecars are staged beside their destinations and atomically replaced.
  The builder rejects existing symlink, junction/reparse, or hard-link redirects.
  Output-directory changes must be serialized: a parent directory replaced between
  validation and replacement cannot be locked against by portable filesystem APIs.

The corpus is scenario-derived training data. It is not evidence that a model was trained
or that generated trajectories succeeded in a real environment.

For an isolated non-live preparation, choose a new output directory outside the
clean source checkout and run:

```powershell
& .\.venv\Scripts\python.exe -m training.build_sft_dataset --output "C:\experiment-root\g7-prep\sft_corpus_train.jsonl"
```

The corpus manifest and training config are written beside that file. Verify the
corpus's canonical-LF
SHA-256 against `523cad3478e2018ebb830bab973bc02811045c6131dd0bf8f59328d756287e81`
before training. The local 2026-09-26 replay reproduced this hash; its examples
remain synthetic.

The two older `training/generate_trajectories*.py` command-line entrypoints are retired.
One applied Chaos and used cluster-wide cleanup; the fast path used synthetic alerts and
model-claimed outcomes as reward. The pure trajectory serializer remains for the frozen
Train-split corpus builder and its contract tests. Neither retired entrypoint is a valid
real-data or empirical training launch command.

## Training Contract

`training/sft.py` requires exact base-model and tokenizer revisions, verifies the corpus
against the frozen Train split and any adjacent corpus manifest's hash, split, and counts,
and uses the project-owned Qwen tool template with assistant-only loss. It writes planned,
running, completed, failed, or interrupted state atomically and records:

- Before writing the planned run manifest, it reads one bounded (16 MiB maximum),
  redirect-free byte snapshot and validates the JSONL rows and frozen Train split from
  that snapshot. The corpus hash, counts, and origin classification are derived from
  those same bytes; `datasets.Dataset` is built from their parsed rows in memory. A later
  change to the `--data` path cannot silently replace this run's input.
- The run manifest still records the absolute source corpus path. G8 can recheck
  those bytes when an exact approved corpus path is explicitly supplied, and
  downgrades origin to `UNVERIFIED` if that path no longer matches. G9's parent
  checkpoint validation does not supply an approved corpus path, so its data
  origin remains `UNVERIFIED` rather than inheriting the training-time claim.
- corpus, split, and template hashes;
- adjacent corpus-manifest path and raw SHA-256, with its data-origin classification;
- `UNVERIFIED` origin and a null synthetic flag when no verifiable classification exists.
  The known `scenario_derived_synthetic` label is accepted only with the frozen corpus
  SHA-256 and its exact 64-example, 16-scenario Train inventory; unsupported or
  conflicting origin claims remain unverified.
- seed, hyperparameters, LoRA and quantization settings;
- source SHA and dirty flag; G8/G9 reject dirty SFT source for empirical use
  because a dirty checkout is not reproducible from its HEAD SHA alone;
- runtime, package, and hardware metadata;
- trainer state and loss history;
- a hashed inventory of every completed adapter/checkpoint file.
- the canonical `sft_run_manifest.json` inside the checkpoint directory so G8 and
  G9 can validate it; an external-only manifest path is rejected.

A run cannot be marked completed without checkpoint files. A bounded launch in the current
environment stopped at missing optional ML dependencies before model loading and persisted
that failure honestly.

Once an approved BF16-capable runtime, exact model/tokenizer revisions, and a
clean source checkout are available, the declared training command is:

```text
python -m training.sft --model Qwen/Qwen2.5-7B-Instruct --model-revision <approved-commit> --tokenizer-revision <approved-commit> --data <verified-external-corpus> --output <new-external-checkpoint-dir> --epochs 3 --lr 0.0002 --batch-size 2 --grad-accum 4 --max-seq-len 2048 --seed 2026
```

Placeholders must be resolved from the actual approved environment. This is a
launch template, not authorization to train or evidence of a checkpoint.

## Current Verification

Local tests validate corpus integrity, split isolation, schema and tool-call pairing,
template rendering, assistant-only masking, lifecycle persistence, and checkpoint hashing.
Real SFT training remains unexecuted.

**Gate G7 Status: PARTIAL**
