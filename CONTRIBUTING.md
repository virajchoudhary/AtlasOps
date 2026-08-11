# Contributing to AtlasOps

AtlasOps is maintained as a fork of `Harikishanth/AtlasOps` with upstream history and
MIT attribution preserved.

## Remotes and workflow

- `origin`: `https://github.com/virajchoudhary/AtlasOps`
- `upstream`: `https://github.com/Harikishanth/AtlasOps`

Use this workflow:

`main -> task branch -> implementation -> local validation -> commit -> push -> pull request -> GitHub Actions -> review -> merge`

Never develop directly on `main` or push to `upstream`. Use one logical change per
branch and PR.

## Branch and commit names

Use a short, descriptive branch name with one of these prefixes:

- `feat/<short-name>`
- `fix/<short-name>`
- `test/<short-name>`
- `docs/<short-name>`
- `infra/<short-name>`
- `experiment/<short-name>`
- `chore/<short-name>`

Prefer clear atomic commit messages for new work, for example:

- `fix: correct benchmark tier initialization`
- `feat: add remediation candidate schema`
- `test: cover environment verifier`
- `docs: record upstream baseline`
- `infra: validate GKE resource naming`
- `experiment: add base-vs-sft benchmark config`
- `chore: establish repository governance`

This convention is preferred, not a reason to rewrite existing history.

## Pull requests and validation

Every PR should explain:

- Problem
- Scope
- Implementation
- Validation
- Evidence / Results
- Risks
- Out of Scope

Run relevant unit tests and all CI checks. Integration claims require integration
evidence; mocked tests alone do not prove live cloud, model, or cluster behavior.

## Experiments and generated artifacts

Material ML experiments must record configuration, seeds, commit SHA, model and
dataset/scenario versions, environment, metrics, raw outputs, and failures.

Do not commit large checkpoints, generated training datasets, or large benchmark
outputs to normal Git. A later artifact policy may approve small reproducibility
fixtures.

## Security

Never commit credentials, tokens, kubeconfigs, service-account material, provider keys,
or other secrets. Use environment variables and approved secret stores.
