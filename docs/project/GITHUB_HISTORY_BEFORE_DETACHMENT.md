# GitHub History Before Fork Detachment

Evidence snapshot date: 2026-08-12

## Detachment Context

`virajchoudhary/AtlasOps` originated as a GitHub fork of
`Harikishanth/AtlasOps`. The original Git history, authorship, MIT license, and
attribution are intentionally preserved. Leaving the GitHub fork network changes the
repository-network classification only; it does not transfer authorship of inherited
code or remove the project's attribution obligations.

After detachment, `upstream` is intended to remain a normal Git remote pointing to the
original repository. GitHub documents that leaving a fork network is permanent and the
standalone repository cannot later be reconnected to that fork network. See
[Detaching a fork](https://docs.github.com/en/pull-requests/how-tos/work-with-forks/detaching-a-fork).

## Original Upstream

| Field | Value |
|---|---|
| Repository | `Harikishanth/AtlasOps` |
| Frozen baseline | `bf9bd197c9f4a05ae55ade254802a9eef1a74356` |
| License | MIT |

## Project PR History

| PR | Title | Stage commit | Merge commit | CI run | Result | Purpose |
|---|---|---|---|---:|---|---|
| #1 | `chore: establish project governance and CI foundation` | `b8034486f46828d0a815c60b9f39381ba62d0d1c` | `0fa2c086baefc0ec8909ecae91e84b498ae6746f` | `31509781377` | Python 3.11/3.12 succeeded | Establish governance, provenance, contribution/security guidance, templates, and measured CI. |
| #2 | `chore: establish reproducible development baseline` | `ad7a7a6da05f1e4289317367424844972f00816e` | `5774dc4e74a0b033402277110157371d81cdc419` | `31516716428` | Python 3.11/3.12 succeeded | Establish pinned Windows/Python 3.12 development inputs and clean-checkout reproduction evidence. |
| #3 | `fix: harden runtime security configuration` | `d66f6004ab53d563fbe4dda48148651f2e777527` | `2760c7a40677a13230c67d220b8a382b8aa059c2` | `31555008914` | Python 3.11/3.12 succeeded | Remove unsafe inherited Argo CD and audit-signing defaults and enforce fail-closed runtime configuration. |

### PR #1 evidence

- GitHub state: merged; base `main` at
  `bf9bd197c9f4a05ae55ade254802a9eef1a74356`; head
  `chore/project-bootstrap` at `b8034486f46828d0a815c60b9f39381ba62d0d1c`.
- Created: `2026-08-11T15:56:45Z`; merged: `2026-08-11T16:12:17Z`.
- Commit count: 1; changed files: 11.
- Scope: repository operating rules, upstream provenance, contribution and security
  guidance, issue/PR templates, `.gitignore` hygiene, and a Python 3.11/3.12 CI matrix.
- Validation: compilation, Ruff `E9,F63,F7`, 202 tests with 2 warnings, package build,
  credential-pattern review, and unchanged license evidence passed locally; CI run
  `31509781377` succeeded for Python 3.11 and Python 3.12.

### PR #2 evidence

- GitHub state: merged; base `main` at
  `0fa2c086baefc0ec8909ecae91e84b498ae6746f`; head
  `chore/reproducible-dev-baseline` at
  `ad7a7a6da05f1e4289317367424844972f00816e`.
- Created: `2026-08-11T17:16:23Z`; merged: `2026-08-11T17:27:59Z`.
- Commit count: 1; changed files: 6.
- Scope: direct dependency declarations, pinned build backend, human-maintained
  development input, compiled Windows/Python 3.12 lock, and reproduction guidance.
- Validation: dependency integrity, compilation, Ruff, package build, safe imports,
  202 tests with 2 warnings, and a detached clean-checkout reproduction all passed;
  CI run `31516716428` succeeded for Python 3.11 and Python 3.12.

### PR #3 evidence

- GitHub state: merged; base `main` at
  `5774dc4e74a0b033402277110157371d81cdc419`; head
  `fix/security-config-baseline` at
  `d66f6004ab53d563fbe4dda48148651f2e777527`.
- Created: `2026-08-12T01:51:53Z`; merged: `2026-08-12T02:01:23Z`.
- Commit count: 1; changed files: 10.
- Scope: explicit Argo CD runtime configuration, HTTPS preservation, secure TLS
  default, removal of hardcoded credential defaults, removal of the public audit HMAC
  fallback, and an audit-secret guard before agent execution.
- Validation: 47 focused security tests and the full 219-test suite passed with 1
  deprecation warning; Ruff, compilation, package build, no-network checks, and a
  targeted secret audit passed. CI run `31555008914` succeeded for Python 3.11 and
  Python 3.12. No historical credential value is reproduced in this record.

## CI Evidence

| Run | Workflow/event | Head SHA | Python 3.11 | Python 3.12 | Overall |
|---:|---|---|---|---|---|
| `31509781377` | `CI` / `pull_request` | `b8034486f46828d0a815c60b9f39381ba62d0d1c` | Success | Success | Success |
| `31516716428` | `CI` / `pull_request` | `ad7a7a6da05f1e4289317367424844972f00816e` | Success | Success | Success |
| `31555008914` | `CI` / `pull_request` | `d66f6004ab53d563fbe4dda48148651f2e777527` | Success | Success | Success |

## Main History

All listed commits were verified reachable from the current `main` snapshot:

```text
bf9bd197c9f4a05ae55ade254802a9eef1a74356  frozen upstream baseline
  -> 0fa2c086baefc0ec8909ecae91e84b498ae6746f  PR #1 merge
  -> 5774dc4e74a0b033402277110157371d81cdc419  PR #2 merge
  -> 2760c7a40677a13230c67d220b8a382b8aa059c2  PR #3 merge
```

The stage commits `b8034486f46828d0a815c60b9f39381ba62d0d1c`,
`ad7a7a6da05f1e4289317367424844972f00816e`, and
`d66f6004ab53d563fbe4dda48148651f2e777527` are also reachable from `main`.

## Branch Protection Before Detachment

| Setting | Snapshot |
|---|---|
| Pull request required | Yes |
| Required approving reviews | 0 |
| Required checks | `Python 3.11`, `Python 3.12` |
| Strict/up-to-date checks | Enabled |
| Force pushes | Blocked |
| Branch deletion | Blocked |
| Administrator enforcement/recovery | Administrator enforcement disabled; administrators retain recovery/bypass capability |

## Repository Settings Before Detachment

| Setting | Snapshot |
|---|---|
| Repository | `virajchoudhary/AtlasOps` |
| Visibility | Public |
| Default branch | `main` |
| Merge commits | Enabled |
| Squash merges | Enabled |
| Rebase merges | Enabled |
| Auto-merge | Disabled |
| Branch protection | PR required; Python 3.11/3.12 required; strict; force pushes and deletion blocked |
| Origin | `https://github.com/virajchoudhary/AtlasOps.git` |
| Upstream | `https://github.com/Harikishanth/AtlasOps.git` |

## Detachment Eligibility Snapshot

GitHub's repository API reports the size field in KB.

| Criterion | Snapshot |
|---|---|
| Administrator access | Yes |
| Public | Yes |
| Size | 1,250 KB |
| Under 1 GB | Yes |
| Child forks | 0 |
| Eligible for Leave fork network | Yes |

## Metadata Loss Warning

GitHub documents that the standalone repository will not retain repository-network
metadata associated with the current fork, including pull requests, issues, comments,
wikis, stars, watchers, child forks, and other associated metadata. Git commit metadata
is preserved. Leaving the network is permanent and cannot be reversed by reconnecting
the repository to the former network.

## Post-Detachment Verification Checklist

- [ ] Repository no longer reports a fork relationship.
- [ ] Repository URL is unchanged if GitHub's native leave-network operation preserves it.
- [ ] `main` remains at `2760c7a40677a13230c67d220b8a382b8aa059c2` or at the later reviewed evidence-PR merge commit.
- [ ] Complete Git history and all archived stage/merge commits remain reachable.
- [ ] `LICENSE` remains present and unchanged.
- [ ] `origin` remains valid.
- [ ] `upstream` still points to `Harikishanth/AtlasOps` as a normal Git remote.
- [ ] Main branch protection is checked and reapplied if needed.
- [ ] The CI workflow remains present.
- [ ] Python 3.11 and Python 3.12 checks run successfully on the next pull request.
- [ ] Project provenance and this evidence record remain intact.
