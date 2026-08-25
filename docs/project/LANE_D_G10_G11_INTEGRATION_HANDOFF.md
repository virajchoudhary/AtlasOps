# Lane D G10/G11 integration handoff

Status: `READY_FOR_FUTURE_INTEGRATION_HOLD` — this is not a G10/G11 PASS.
Lane D SHA at handoff: `1c0496075fde94a3d4f504a95f91edae1cec7fcd`.

## Contract

- Packet contract: `rs-v0`.
- Interaction schema: `rs-interaction-v1`.
- Model artifact: `atlasops-rs-artifact-v1` / `rs-model-v1`.
- Corpus manifest: `rs-corpus-manifest-v1`.
- G5 binding: `g5-rs-binding-v1`; unknown incidents and absent canonical split
  hashes fail closed.

## Integration assumptions

- Integrate only after G4, G5, and G9 predecessor gates authorize their outputs.
- Review/land PR #32 first when it changes the Diagnosis prompt, tool schemas, or
  policy; then rerun the catalogue/schema tests against the merged contract.
- Bind interaction extraction to the immutable Lane B/G5 split hash before any
  real corpus use. Until binding, manifests remain `pending_g5`.
- Do not enable a coordinator adapter during G4. The future order is Diagnosis,
  RS packet construction, safety/approval, GRPO/remediation policy, execution.

## Owned files

```text
agents/rs/__init__.py
agents/rs/catalogue.py
agents/rs/features.py
agents/rs/integration.py
agents/rs/metrics.py
agents/rs/ontology.py
agents/rs/persistence.py
agents/rs/recommender.py
agents/rs/schemas.py
agents/rs/splits.py
agents/rs/synthetic.py
tests/test_rs_contracts.py
tests/test_rs_recommender.py
```

The package documentation is also owned by Lane D:
`agents/rs/README.md`. No current runtime entrypoint imports `agents.rs`; its
future coordinator adapter must be separately reviewed.

## Known overlaps

No file overlap exists with current `origin/prep/g5-g6-contract` or
`origin/research/g9-grpo-audit`. Semantic integration points are:

- PR #32 tool schemas and Diagnosis prompt;
- Lane B/G5 canonical split/exposure metadata;
- Lane C/G9 GRPO packet consumer expectations.

## Required pre-integration checks

Run from repository root:

```powershell
python -B -m pytest tests/test_rs_recommender.py tests/test_rs_contracts.py tests/test_tool_policy_contract.py -q -p no:cacheprovider
ruff check --no-cache agents/rs tests/test_rs_recommender.py tests/test_rs_contracts.py
git diff --check
```

After merging PR #32 or any G5/G9 branch, rerun all three commands plus the full
unit suite used by CI.

## Safety and data exclusions

Forbidden dependencies: live incident evidence, training/final-test corpora,
Docker, Kind, Ollama, network clients, subprocesses, file I/O in RS model
contracts, and direct imports from `agents.tools`.

RS may show mutating candidates before approval. Every mutating candidate carries
`approval_required_before_execution=true`; approval, ACL, safety, budget, and
policy gates remain authoritative downstream. The package cannot render commands
or execute tools.

## Rollback criteria

Block integration if:

- packet/artifact schema versions drift without an approved migration;
- catalogue tools leave the remediation ACL or mismatch strict parameter schemas;
- fit can read test or `future_final_test` rows;
- unobserved recommendations acquire utility labels;
- G5 binding accepts unknown incidents or a missing/invalid split hash;
- restored model state bypasses integrity/type/dimension checks under Python -O;
- RS gains execution, rendering, network, subprocess, or live-data dependency;
- focused tests, Ruff, or diff checks fail on Linux CI or Python 3.11/3.12.

## Validation record

At SHA `1c0496075fde94a3d4f504a95f91edae1cec7fcd`: shadow merge into current
main was clean; focused tests passed (`46 passed`); broader relevant offline
suite passed (`82 passed`); targeted Ruff passed; canonical compileall passed;
and Git diff checks passed. These are rehearsal checks, not scientific gate
evidence.
