# AtlasOps peer handoff tracker

This tracker covers team continuation, not scientific certification.
The [completion matrix](COMPLETION_CHECKLIST.md) remains the only authoritative
gate-status table. The [onboarding guide](PEER_ONBOARDING.md) is the safe entrypoint.
Do not reuse a historical attempt, consumed approval, checkpoint claim, or source pin.

The accepted software checkpoint is merged [PR 224](https://github.com/virajchoudhary/AtlasOps/pull/224)
at `c90d95602762c5d9c79bec7ce76d805446422c39`.
[Post-merge CI](https://github.com/virajchoudhary/AtlasOps/actions/runs/39256571553)
passed. Subsequent engineering and its exact source are identified by each
reviewed PR and the external final package receipt, not by a guessed future SHA.

## Definitive continuation status

DONE means accepted software or preparation within its stated scope.
BLOCKED means evidence or authority is still missing. Neither means empirical PASS.
No peer name or approval is assumed.

| Workstream | DONE | BLOCKED | OWNER/APPROVAL | NEXT ACTION |
| --- | --- | --- | --- | --- |
| Software and delivery | PRs 221, 220, 219, 222, 223 and 224 integrated; main CI passed; prior source archives independently checked | Later engineering requires its own review, CI and final archive | Maintainer may complete offline engineering; scientific claims need project-lead acceptance | Use reviewed main and regenerate the exact-source package after the final engineering merge |
| A. Environment qualification and G4 | Fail-closed source, telemetry, approval and run-owned cleanup contracts exist; negative records preserved | Candidate source mismatch; real environment/model/operator/alert-origin qualification and G4 reopening absent | Host owner approves restoration and resources; project lead reopens protocol; operator approves exact action | Follow the source and host checks below, then independently freeze a fresh causal protocol before requesting one incident permit |
| B. G6 Base baseline and matched G8 | Validation diagnostic and bounded v17 artifact/reload retained; counterbalanced campaign requirements prepared | No accepted causal incident baseline, matched incident results or population rule | Project lead approves population/scoring; host and operator approve each bounded campaign | After G4, use distinct six-episode G6 and twelve-arm G8 campaigns; never reuse G6 episodes as G8 arms |
| C. G9 controlled GRPO | Direct-action simulator, observation journal, [offline environmental-learning safeguards](G9_OFFLINE_SAFEGUARDS_HANDOFF.md) and frozen negative evidence retained | Four-generation configuration and safeguards require exact-source review and CI; no accepted empirical environmental learning or policy | Project lead reopens G9; separate training/resource approval; fresh exception for any earlier simulator-only track | Use the G9 reproduction guide to verify the model-free safeguards; then request only the reviewed finite Train-only pilot |
| D. G12 policy and G13 measurement | Policy capture and [accepted fail-closed G13 safeguards](G13_OFFLINE_SAFEGUARDS_HANDOFF.md) retain denominators, provenance and pre-cleanup endpoints | Accepted learned policy/third arm, integrated incident results, frozen measurement and held-out access absent | Project lead approves measurement and access; operator approves incidents; independent reviewer recomputes evidence | Use the G13 reproduction guide, then obtain separate policy/incident/measurement permits |
| E. G14 peer host and G15 delivery | Local read-only app, synthetic rehearsal, CI container and review materials accepted within software scope | No named peer host, live operator/security acceptance, complete scientific claims acceptance or submission receipt | Peer host owner approves target/audience; project lead approves claims; submission owner authorizes actual submission | Review the package and safe demo first; request peer-host acceptance separately from public deployment and academic submission |

## Read-only source and host qualification

From the exact reviewed checkout, run the existing source-only inspector:

```powershell
python -B -m config.g4_demo_candidate
```

Require an honest mismatch report. Do not replace historical hashes to make it green.
`active=false`, `runtime_qualified=false`, and
`launch_authorized_by_this_report=false` remain distinct from file readiness.
No output from this command is an experiment permit.

The existing `bash infra/local/setup_local.sh --check` inspects prerequisites but does
not qualify model identity, advancing telemetry or a live operator. Its default
Docker context is not an experimental target-selection permit.
Do not use `--apply` as part of onboarding or diagnostics.
On Windows, invoke it in Git Bash. Do not start a stopped WSL distribution merely
to run this check. WSL startup requires separate restoration authorization.

After the owner selects the exact host and confirms GET-only access, inspect:

```powershell
docker --context desktop-linux info
docker --context desktop-linux ps --all --no-trunc
kind get clusters
kubectl --context kind-atlasops-local --request-timeout=10s get nodes -o json
kubectl --context kind-atlasops-local --request-timeout=10s get deployments,statefulsets,pods,services -A -o json
kubectl --context kind-atlasops-local --request-timeout=10s api-resources --api-group=chaos-mesh.org -o name
```

If the engine or cluster is unavailable, retain the error and stop these probes.
Do not start, reinstall, reset, prune, recreate, switch the global context, clear
Chaos resources, change WSL limits, or terminate unrelated processes.
Before an approved restoration, identify which existing containers might resume.
Enumerate every returned Chaos kind with GET only and retain its namespace, UID
and resourceVersion. Existing objects are not automatically run-owned.

Qualify two advancing raw telemetry observations at least 30 seconds apart,
joined to the selected pod UID and container ID. Record scrape/source timestamps,
Alertmanager receiver provenance, Metrics API and relevant Jaeger/Argo read paths.
Ready replicas or empty successful HTTP responses do not establish telemetry.
Port-forwards, scrape changes, service starts and secret rotation require their
appropriate separate authorization.

Select resources from measured capacity. The earlier proposed 16 GiB free-RAM
allowance exceeded the measured local host's total usable RAM; its 4 GiB GPU did
not meet the proposed 24 GiB VRAM allowance. These are dated observations, not a
new measurement on the peer. Approve another host or a reviewed, measured lower-
memory plan without silently changing model, precision or learning objectives.
Inventory existing storage before any multi-GB transfer.

Model qualification must bind the loaded Base, tokenizer and adapter identities
to each generation. A mutable alias is insufficient. The real operator channel
must bind an authenticated identity, exact action digest, finite expiry, rejection,
timeout and replay prevention. Synthetic UI decisions do not authorize real actions.
The current incident runner's direct alert invocation is not proof of actual
Alertmanager-originated admission.

## Campaign requirements and stop rules

The [versioned execution proposal](PROJECT_CLOSURE_EXECUTION_PROPOSAL_V1.md)
defines preparation requirements, not approved execution.

- G4 proposes one new causal episode. Freeze actual fault start/end, target
  identity, approval/action deadlines, objective recovery before expiry and
  pre-cleanup verdict. Cleanup is restricted to exact run-owned identities.
- G6 proposes six new Base-only Validation episodes after accepted G4 evidence.
  Retain all failures and nulls. A descriptive pilot does not close the full gate.
- G8 proposes twelve distinct Base/v17 arms over the same six scenarios.
  Counterbalance order before outcomes, prove reset equivalence, and retain
  every failed arm. The old diagnostic F1 values remain negative evidence.
- G9 proposes two groups of four Train-only simulator completions and at most
  two optimizer steps. Stop in the first group on invalid actions, missing
  verifier evidence, no verifier-component variation, or missing reward-only
  gradient evidence. Claim, syntax, invalid-action penalty or KL variation
  cannot substitute for environment-coupled learning.
- G13 needs an accepted third arm and frozen denominators, lineage, clocks,
  invalidation, null and coverage rules. Infrastructure exclusions must be
  outcome-independent. Unresolved TTR is censored only at actual comparable
  pre-cleanup observation coverage, never an unobserved nominal deadline.

Stop on source drift, missing/rejected/expired authority, stale telemetry,
ambiguous fault identity, negative/inconclusive outcome or uncertain cleanup.
Do not replace a failed episode, extend budgets or retry a scientific run
without a fresh approved decision.

## Reviewer checklist

- Match the source, patch, CI and package receipt to the actual reviewed commit.
- Separate software/model-free evidence from real incident or learning evidence.
- Inspect all required raw observations, failures, nulls, denominators and cleanup.
- Verify checkpoint, tokenizer, adapter, scorer and scenario lineage independently.
- Confirm the operator and project lead supplied the exact required permissions.
- Keep G4/G9 frozen until valid reopening; keep G10/G11 OUT_OF_SCOPE.
- Do not promote G6/G8/G12/G13/G14/G15 from downstream software or a summary alone.

## External dependency register

The project fork currently has GitHub Issues disabled. No repository setting was
changed and no empty issue or placeholder PR was fabricated.
Meaningful implementation/protocol PRs carry their own unfinished work and
review checklist. Until the owner enables Issues or selects another team tracker,
the BLOCKED rows above are the external-dependency register.
An unavailable issue tracker is not permission to start the blocked work.

Use [deferred research handoff](DEFERRED_RESEARCH_HANDOFF.md),
[immutable evidence index](../EVIDENCE_INDEX.md), and
[the negative G9 result](CONTROLLED_G9_FINAL_NEGATIVE_V1.md) before any reopening.
Credentials, weights and private owner-local archives are not supplied by this tracker.
