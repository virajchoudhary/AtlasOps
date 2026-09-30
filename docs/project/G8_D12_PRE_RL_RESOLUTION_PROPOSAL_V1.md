# G8 D12 pre-RL resolution decision v1: review proposal

**Status: PROPOSED / NOT APPROVED / NON-EXECUTABLE.** This document
frames the pending D12 decision. It does not amend the [master G8
criterion](MASTER_PIPELINE_STATUS.md), authorize inference or an incident
run, or change G8's `IMPLEMENTED / EMPIRICAL EVIDENCE MISSING` status.

## Problem Statement

The master pipeline asks G8 to verify SFT incident resolution rate and
format compliance before RL. The current [Stage 8 evaluator](STAGE_8_SFT_EVALUATION.md)
is checkpoint-backed but diagnosis-only: its empirical Validation summary
leaves resolution, reward, safety, and time to resolve null. Its
`format_compliance_rate` is diagnostic JSON schema conformance, not action
or Comms conformance. Historical mock values cannot fill those fields.
No completed usable SFT adapter is currently preserved.

## Proposed Decision

**A (recommended): retain the original pre-RL criterion.** Prepare a
separate Validation-only integrated SFT evaluator that uses the same
approved base and completed SFT adapter, executes the governed agent chain
under an independently reviewed live protocol, and records the raw alert,
diagnosis, approval, exact action, tool result, post-action objective
verification, Comms, failure, timing, and cleanup evidence. A model-resolution
claim requires an authorized action followed by verifier-confirmed recovery,
subject to the still-pending D4 eligibility rule. Preserve every
blocked, failed, missing-output, and inconclusive attempt; do not convert
an unknown observation to a success or a numeric zero. Keep the existing
diagnosis-only results as secondary diagnostics. The primary action/Comms
format population and exact eligibility, invalidation, and scoring rules
still require the D4-D7 review and freeze before empirical acceptance.

**B (not recommended): version a narrower G8 criterion.** Treat G8 as
diagnosis/schema readiness only and move incident resolution to G12/G13.
This could remove the G8 resolution prerequisite for an RL handoff without
a demonstrated SFT recovery baseline, changing the stated v2.2 dependency.
It requires an explicit
project-lead protocol revision, downstream gate review, and honest
reclassification; existing mock scores would still not become empirical.

The project lead must choose A or B and specify whether G8's primary
format check covers the integrated action/Comms outputs (A) or only
diagnostic JSON (B). This proposal recommends A but records **no choice**.
Approval of a non-live implementation direction would not authorize a
cluster, P1 decision, fault, model download, inference, training, Test or
Leaderboard access, or a scientific G8 PASS.

## Testing Decisions

For A, test the evaluator's public artifact boundary with synthetic,
explicitly non-empirical episodes. Require a provenance-checked SFT parent,
frozen Validation membership and a fresh output location; reject Test and
Leaderboard before any model or environment access. Verify that missing
approval, rejected or timed-out P1, no action, failed tool execution,
inconclusive verification, and cleanup failure retain distinct raw
outcomes without a recovery claim. A local stub cannot prove actual
checkpoint behavior, safe operation, or resolution rate.

## Out of Scope

This is not the D4-D9 measurement freeze or the D10 live authorization.
Do not reuse G9's SFT+GRPO arm as an SFT-only G8 result, or adapt
diagnosis-only G6/G8 rows into incident recovery. No final-Test outcome
may be read while reviewing this choice. G8 remains evidence-missing
until a separately authorized run and independent review establish it.
