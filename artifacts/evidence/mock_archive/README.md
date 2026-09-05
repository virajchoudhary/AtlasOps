# Historical mock evaluation archive

These files are historical mock/deterministic outputs preserved by SETUP-03
on 2026-09-05. They are useful for test/provenance purposes only.

**They are not evidence of actual base, SFT, or GRPO checkpoint performance.
Using these files to close G6, G8, or G9 is prohibited.**

| Archive | Original directory | Classification |
|---|---|---|
| `stage6/` | `artifacts/evidence/stage6/` | MOCK: summaries explicitly record `mock_eval=true` |
| `stage8/` | `artifacts/evidence/stage8/` | MOCK: deterministic SFT evaluator; no proven trained checkpoint used |
| `stage9/` | `artifacts/evidence/stage9/` | MOCK: deterministic GRPO evaluator; no proof of direct policy-action-environment-verifier-reward coupling |

Model labels, numerical improvements, resolution rates, format-compliance values,
and PASS wording inside historical artifacts do not establish empirical performance.
Raw contents are preserved unchanged for provenance, not endorsed.

Tracked references were inspected before relocation. The old directories are
evaluator output defaults; tests generate their own temporary outputs rather than
requiring these ten files as checked-in input fixtures. Some comparison-table tests
can generate new default outputs when the table is missing. No evaluator or test
that regenerates evidence was run during recovery, and runtime paths were not
changed. Future generated outputs still require independent classification.

See [workspace recovery provenance](../recovery/2026-09-05-workspace-recovery.json)
for each original-to-archive path mapping, byte size, and original/final SHA-256.
Scoped Git attributes prevent line-ending conversion of these raw files.
This archive does not reconcile the existing master status or academic reports.
