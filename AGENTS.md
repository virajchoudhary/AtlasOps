# AtlasOps repository operating contract

## Project identity

This repository is the university team's continuation and extension of
[Harikishanth/AtlasOps](https://github.com/Harikishanth/AtlasOps), frozen at upstream
baseline `bf9bd197c9f4a05ae55ade254802a9eef1a74356`. The project fork is
`virajchoudhary/AtlasOps`. Preserve the full upstream Git history, MIT license, original
attribution, and a clear boundary between upstream work and team contributions.

## Architecture and academic direction

Preserve AtlasOps as the foundation:

`Incident / Alert -> Triage Agent -> Diagnosis Agent -> Safety / Approval Gate -> Remediation Agent -> Comms Agent`

Preserve the existing ML direction:

`Base model -> trajectory generation -> SFT -> GRPO`

The project strategy is to repair, complete, reproduce, and validate the original
implementation before adding substantial original work. Do not casually redesign
AtlasOps into an unrelated project.

The current project-lead [GAI + RL scope revision](docs/project/GAI_RL_SCOPE_REVISION.md)
supersedes RS-required portions of the adopted v2.2 working specification.
Current required academic workstreams are:

- **Generative AI:** multi-agent reasoning, tool calling, diagnosis, eventual RAG and
  historical incident memory, incident communications, and postmortems.
- **Reinforcement Learning:** retain GRPO while correcting and validating the
  policy-environment-reward relationship.

The existing Recommender Systems layer and its bounded synthetic evidence are
historical, optional research; G10/G11 are `OUT_OF_SCOPE` for final completion.
Do not delete or reclassify their historical results as empirical incident gains.

## Sources of truth

Use this order:

1. Current approved project Master Pipeline / Project Bible.
2. This `AGENTS.md` operating contract.
3. Current tracked implementation and frozen configuration.
4. Saved experiment and evaluation evidence.
5. Upstream documentation for historical intent.

For factual status and gate closure, executable behavior and preserved evidence outrank
prose claims, including this contract and the Master Pipeline status. Record discrepancies;
implementation, mock outputs, and predetermined metrics do not establish empirical PASS.
Environment verification is authoritative for incident resolution. Preserve negative results.
Downstream implementation does not close an upstream empirical gate.

## Development orchestration

Sol Advisor is a preferred development enhancement, not an AtlasOps runtime or
development dependency. If a verified tooling or platform defect makes it unavailable,
native Codex work may proceed when the user explicitly authorizes that fallback. Do not
patch or weaken Sol Advisor to bypass its security checks.

During an authorized native fallback, inspect before editing, keep changes minimal and
task-scoped, run relevant tests, perform an explicit self-review, use PR and CI review,
and make only evidence-supported claims.

## Git and quality rules

- Never develop directly on `main`; treat it as stable.
- Use one logical branch and PR per change. Prefix branches with `feat/`, `fix/`,
  `test/`, `docs/`, `infra/`, `experiment/`, or `chore/`.
- Never force-push to `main`, rewrite history without explicit authorization, or push
  to the `upstream` remote.
- Inspect status and the current branch before work. Preserve unrelated changes and
  commit only task-owned files with clear, atomic messages.
- Changes enter `main` through a pull request.
- Inspect existing behavior before editing, prefer targeted changes, and update tests
  for meaningful behavior changes.
- Run relevant checks before claiming completion. Distinguish unit/mock evidence from
  real integration evidence and preserve meaningful failures and negative results.
- Never fabricate benchmark or experiment results.

A feature is not working merely because code exists, documentation claims it, a mock
test passes, or an LLM says it succeeded. Use independent verification appropriate to
the claim.

## Experiment, security, and infrastructure rules

For each material ML experiment, record the code SHA, model/checkpoint,
dataset/scenarios, configuration and hyperparameters, seeds, environment, date/time,
raw outputs, metrics, and failures. Do not commit large checkpoints, generated datasets,
or large result bundles to ordinary Git.

Never commit or print GitHub tokens, model-provider keys, GCP credentials, kubeconfigs,
or cluster secrets. Use environment variables and approved secret stores.

Do not provision or mutate real infrastructure, execute real remediation, run Chaos
Mesh against a cluster, or perform multi-GB model/training operations without explicit
authorization. Estimate storage before any multi-GB operation.

## Known review items (do not fix without a scoped task)

- G4 remains NOT_PASSED: attempt 010 is the latest completed negative result among
  009-014; 009 and 011-014 are interrupted/inconclusive. Cleanup failures remain recorded.
- GRPO now has a locally tested direct-action policy/environment adapter. Real settling,
  explicit live-run authorization and pinned cluster context, P1 integration, and dense
  reward evidence still require verification before a live training claim.
- G7 remains PARTIAL: the bounded synthetic free-T4 v17 SFT pilot produced a preserved
  adapter that passed an independent offline reload
  ([evidence](artifacts/evidence/stage7/free-t4-v17/RESULT.json)). This establishes an
  artifact, not incident improvement or G8 evaluation. Archived Stage 6/8/9 mock outputs
  cannot close empirical gates.
- Stage 13's current aggregator rejects missing real variant metrics; the saved hardcoded
  profiles remain historical, and actual variant ablation/stress evaluation is missing.
- Stage 10 custom dataset output now writes its own adjacent evidence manifest.
  Historical-feedback data was never established; G10/G11 are no longer required.
- SAFETY-01 makes P1 approval fail closed: only explicit approval permits remediation;
  timeout/rejection remain blocked with distinct persisted outcomes. Mock/unit control-flow
  coverage is not empirical G4 evidence or a certification of safe deployment.
- Optional recommender implementation/evaluation exists. The original saved result used 28
  scenario-derived rows covering 4 runbooks; the corrected 21-row corpus covers 9 of
  12 runbooks. Neither is genuine historical interaction feedback.
- Infrastructure scripts have static/local validation; real provisioning or portability
  claims require target-specific verification and explicit authorization.
