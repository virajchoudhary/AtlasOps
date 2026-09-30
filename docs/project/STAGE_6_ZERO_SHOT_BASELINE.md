# Stage 6: Reproduce GAI Zero-Shot Baseline (Gate G6)

**Status: IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**

The zero-shot evaluator now separates deterministic compatibility tests from genuine
base-model inference. No approved Qwen2.5-7B-Instruct inference run is preserved in the
repository, so G6 has no empirical performance result.

The older `bench.runner` CLI is retained only for explicit `--mock` compatibility fixtures.
It cannot apply Chaos or clean a cluster, and writes comparison output inside its unique
non-empirical run directory. Use `bench.zero_shot_baseline` for real G6 inference.

## Evaluation Contract

- Callers must explicitly select `mock` or `empirical`; there is no silent fallback.
- Empirical G6 refuses the final `test` split before split lookup, model
  observation, inference, or output creation. Final-Test access requires a
  separately reviewed protocol and authorization. Mock Test fixtures remain
  explicitly non-empirical.
- Configured empirical mode is restricted to a loopback-local Ollama endpoint exposed
  through its OpenAI-compatible `/v1` API, plus an explicit unique output directory.
  Both HTTP clients disable environment proxy discovery (`trust_env=False`).
- The explicit `model_revision` must be a 64-hex SHA-256 digest (optionally prefixed by
  `sha256:`). Before creating run output or sending inference requests, the evaluator
  requires exactly one matching Ollama `/api/tags` entry and verifies its digest.
- Every completion response must name the exact requested model; that response field is
  a name, not a digest. The evaluator records the per-response name and rechecks
  `/api/tags` after inference, but those boundary observations cannot prove that a
  mutable Ollama tag served the same digest throughout each completion. Configured local
  Ollama runs are therefore classified `alias_observed_not_immutable` and never set
  `empirical_claim_allowed=true`. G6 needs a separately approved immutable-serving
  attestation that binds a content digest to each generation before exact-model claims
  are possible.
- The model sees only public alert fields. Expected root cause, Chaos configuration,
  verifier predicates, and known remediation are withheld until scoring.
- A returned prediction must contain a severity in `P0`–`P3`, an explicit
  list of non-empty affected-service names, non-empty root cause, and finite
  numeric confidence. Duplicate JSON keys or non-finite JSON numbers at any
  depth are invalid rather than last-value-wins or nonstandard JSON. Invalid
  returned predictions retain their raw response but are not scored.
- Raw requests, successful model text, parse failures, generation configuration, split
  and dataset hashes, source identity, seed, timestamps, runtime metadata, and model
  identity observations are persisted. Failed HTTP response bodies are not persisted;
  only a bounded error category, status, format, SHA-256, and byte length are retained.
- Every emitted episode row carries the generated `run_id` and requested `model`
  tag also recorded in the run summary, including invalid and failed rows.
  This binds rows to the requested run identity; it does not attest which
  content digest served any generation. G13's non-empirical adapter checks
  these row identities against caller declarations and revalidates successful
  raw diagnostic responses before producing a diagnosis event.
- Configured empirical inference requires a clean Git source before model
  observation and records its preflight and postflight source states. Split
  and dataset digests are captured before inference. A changed or unverifiable
  postflight state is recorded as such; these source checks cannot make a
  mutable model alias claimable or lock the checkout against concurrent edits.
- A returned but invalid prediction records that inference produced a response while
  leaving `diagnostic_metrics` null and the prediction unscored. Diagnostic averages
  use only valid scored rows, record their count, and are null when none exist.
  A failed call with no returned response remains distinct from a parse failure.
- Completion responses are streamed with a 4 MiB raw-byte cap. Oversized responses stop
  at the limit and retain only a prefix hash, observed byte count, and truncation flag.
- Finalized episode rows replace the initial JSONL atomically. If finalization fails,
  the initial rows remain intact and nonclaimable.
- Diagnosis-only evaluation leaves environment resolution, reward, and time to resolve
  unevaluated.
- Injected test doubles and mock runs are marked non-empirical; injected inference skips
  local Ollama identity attestation and can never be claimable empirical evidence.
- An implicit mock output goes to a unique non-empirical directory and cannot update
  the canonical Stage 6 evidence path.
- Explicit output and evidence directories are checked before model observation,
  inference, directory creation, or writes. Tracked repository files, the frozen
  Stage 6 mock archive, occupied output filenames, and detected path redirects
  are rejected. Final summary files use exclusive creation. These preflight
  checks protect against selected-path mistakes, not concurrent hostile
  filesystem replacement after the check.

## Historical Outputs

The Stage 6 files under `artifacts/evidence/mock_archive/stage6/` were generated by
deterministic mock episodes. Their 0% resolution, 0.850 diagnostic F1, 45-second TTR, and
reward values are historical fixture output. They are not model measurements.

## Current Verification

Local tests cover mode selection, truth withholding, local Ollama tag checks, ambient
proxy isolation, response-model matching, mutable-alias nonclaimability, atomic output
finalization, bounded error fingerprints, raw-output provenance, failure retention,
and finite numeric confidence validation (Booleans are not numeric predictions),
required prediction schema, source drift classification, metric computation
after inference, duplicate/non-finite JSON rejection, split isolation, early
final-Test refusal, and artifact-path isolation. The
identity and inference endpoints in these tests are mocked; no model or network request
is made. G6 still requires an approved immutable-serving attestation before its exact
model evaluation can be claimable.

**Gate G6 Status: IMPLEMENTED / EMPIRICAL EVIDENCE MISSING**
