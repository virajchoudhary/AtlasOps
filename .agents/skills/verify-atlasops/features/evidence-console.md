# Evidence console

The console displays preserved records and their provenance without changing research status.

## Sub-features

- Hash-router navigation reaches each primary page.
- Evidence filters preserve historical and external classifications.
- Presentation reads use GET requests rather than operational API writes.

## How to get to it (user POV)

Open `/` for Overview or `/#/evidence` for Evidence.
Use the primary navigation to reach Incidents and the other console pages.

## Driving it with Playwright

Preconditions: use the build and owned server described in the skill.

- Require the `AtlasOps` heading and distinct `NOT_CERTIFIED` and `READY_FOR_REVIEW` labels on Overview.
- On Evidence, select `Historical` and `External`. Require the matching classifications, then select `All` and open `Source details`.
- On Incidents, click `Browse preserved records`. Require completed reads and only GET requests to `/api/`.
- Run `npm run test:e2e -- --grep "primary route|evidence filters|non-GET|overview shows"` from `frontend`.

## Gotchas

Saved evidence can be negative or incomplete. Do not replace missing values with successful outcomes.
The live-operator tests mock API responses and do not certify actual remediation.
