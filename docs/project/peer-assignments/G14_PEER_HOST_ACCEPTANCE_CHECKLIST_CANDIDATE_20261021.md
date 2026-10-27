# G14 peer-host acceptance checklist — CANDIDATE, not executed

**Status:** prepared offline on 21 October 2026. Nothing here was run on a peer host;
no host, audience or reviewer is named in the repository. G14 remains **PARTIAL**;
G15 remains **NOT_CERTIFIED**. Every check is non-destructive and read-only toward
any experimental cluster. Owner values are `UNSPECIFIED` until supplied.

## Identification (required before starting)

| Field | Value |
| --- | --- |
| Peer host (machine, OS, owner) | `UNSPECIFIED` |
| Host owner approving startup and network exposure | `UNSPECIFIED` |
| Approved audience | `UNSPECIFIED` |
| Independent reviewer | `UNSPECIFIED` |
| Exact source commit and its fresh source-review package receipt (ZIP SHA-256) | `UNSPECIFIED` — must be regenerated for the final accepted main; `b23d6cb` packages do not cover later merges |

## Checks (record command, timestamp, output and observer for each)

1. **Exact source:** clean full-history clone; `git rev-parse HEAD` equals the receipt commit;
   `python -m scripts.package_source_review --expected-source-sha <sha> --output-dir <new> --verify`
   reports `READY_FOR_REVIEW` and the receipt's archive SHA-256.
2. **Clean install:** Python 3.12 venv from the repository lock for that platform; `pip check`
   clean; `npm ci --prefix frontend` and `npm run build --prefix frontend` succeed from that checkout only.
3. **Read-only startup:** `python -B -m demo.launcher --check` → `local_presentation_ready: true`;
   then `python -B -m demo.launcher --host 127.0.0.1 --port <free port>`. No operator configuration,
   live observations or incident capture directory supplied.
4. **Network boundary:** listener bound to loopback only (`lsof -iTCP -sTCP:LISTEN` shows `127.0.0.1`);
   any wider exposure needs separate host-owner approval and is out of this checklist.
5. **Authentication / fail-closed approvals:** without operator configuration the launcher registers
   no `/api/operator/start` or `/api/operator/decision` route (`demo/read_api.py` adds them only
   when an operator is configured, and answers an invalid operator session with 403); the separate
   operator approval apps reject a missing or wrong `X-AtlasOps-Key` with 401
   (`tests/test_stage4_approval_channel.py`, `tests/test_g9_approval_channel.py`); the rehearsal's rejected and timed-out approvals never reach remediation
   (CI E2E `rejected and timed-out approval never reach remediation`); the presentation makes no
   non-GET API requests (CI E2E `the presentation makes no non-GET API requests`).
6. **Truthful labels:** overview shows READY_FOR_REVIEW / NOT_CERTIFIED / G4 NOT_PASSED / READ-ONLY;
   rehearsal shows SYNTHETIC / NON-LIVE / NON-EMPIRICAL; unknown paths return 404.
7. **Error handling:** missing rehearsal fixtures do not invent a replacement run (CI E2E); missing
   credentials leave the read-only demo usable and do not disable authentication.
8. **Browser suite:** `npm run test:e2e` from `frontend/` with screenshots preserved before any rerun.
9. **Operator observations:** reviewer records what was seen, any console errors, and stops only
   the process they started.
10. **Sign-off:** host owner and independent reviewer sign the record; a failure is retained, not rerun silently.

This checklist does not authorize public deployment, credentials, live incidents or submission.
