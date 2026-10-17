# Synthetic rehearsal

The rehearsal walks through a simulated incident without executing tools or changing empirical gates.

## Sub-features

- Explicit approval permits simulated progression.
- Rejection or timeout skips remediation.
- Simulated tool success with an unhealthy observation remains unresolved.
- Exported transcripts retain non-empirical labels.

## How to get to it (user POV)

Open `/#/demo` or select Rehearsal in the console navigation.

## Driving it with Playwright

Preconditions: use the build and owned server described in the skill.

- Click `Start rehearsal`, then `Next stage` three times. The `Simulated P1 decision` region appears and progression is blocked.
- Click `Approve simulation`, then advance twice. Require `SIMULATED RECOVERY`.
- Reset and repeat with `Reject` and `Simulate timeout`. Require `SIMULATED REMEDIATION BLOCKED`, with no accepted fixture action.
- Select `single_fault/sf-004` under `Scenario` and `unhealthy` under `Simulated verifier observation`. Approve and advance. Require `SIMULATED UNRESOLVED INCIDENT`.
- Run `npm run test:e2e -- --grep "rehearsal|approval never|failed verification"` from `frontend` for these existing recipes.

## Gotchas

Recovery is synthetic. The download test writes a temporary simulated transcript.
Reset or reload clears browser state. Missing fixtures must disable rehearsal.
The desktop/mobile screenshot test captures the actual built console.
