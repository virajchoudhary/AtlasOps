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

Academic workstreams are:

- **Generative AI:** multi-agent reasoning, tool calling, diagnosis, eventual RAG and
  historical incident memory, incident communications, and postmortems.
- **Recommender Systems:** a new remediation/runbook recommendation layer with its own
  dataset, algorithms, and evaluation.
- **Reinforcement Learning:** retain GRPO while correcting and validating the
  policy-environment-reward relationship.

## Sources of truth

Use this order:

1. Current approved project Master Pipeline / Project Bible.
2. This `AGENTS.md` operating contract.
3. Current tracked implementation and frozen configuration.
4. Saved experiment and evaluation evidence.
5. Upstream documentation for historical intent.

When documentation and executable behavior disagree, record the discrepancy; do not
silently choose one.

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

- The benchmark runner has a runtime ordering bug involving `tier`.
- GRPO completion-to-environment coupling needs scientific correction and validation.
- Resolution reward needs independent environment-ground-truth verification.
- Recommender Systems is absent upstream and will be a new extension.
- Infrastructure scripts require validation before any real provisioning.
