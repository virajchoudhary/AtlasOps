# End-to-End Incident Flow

How an authenticated Alertmanager alert travels through AtlasOps to a verified
incident record. The web injection and cleanup shortcuts are retired; a controlled
real Chaos experiment uses the governed Stage 4 harness and its preflight.

## Sequence Diagram

```mermaid
sequenceDiagram
    participant Operator as Operator
    participant Alertmanager as Alertmanager
    participant App as app.py
    participant Coord as Coordinator
    participant Corr as Correlator
    participant CB as Circuit Breaker
    participant LLM as HF Router / vLLM
    participant Tools as SRE Tools
    participant Discord as Discord Webhook

    Alertmanager->>App: POST /webhook (Bearer secret)
    App->>App: Authenticate payload
    App->>Corr: ingest(alert)
    Corr-->>App: incident_id, should_dispatch=true
    App->>Coord: handle_incident(alert, incident_id)
    Coord->>CB: start_incident()

    rect rgb(30,40,60)
        Note over Coord,LLM: Triage Agent (max 10 turns)
        Coord->>LLM: chat/completions (triage prompt + tools)
        LLM-->>Coord: tool_call: kubectl_get
        Coord->>Tools: kubectl_get(pods)
        Tools-->>Coord: pod status JSON
        Coord->>LLM: tool result + continue
        LLM-->>Coord: conclusion {severity, title, blast_radius}
    end

    rect rgb(30,40,60)
        Note over Coord,LLM: Diagnosis Agent
        Coord->>LLM: chat/completions (diagnosis prompt)
        LLM-->>Coord: tool_call: promql_query, jaeger_search
        Coord->>Tools: promql + jaeger
        Tools-->>Coord: metrics + traces
        LLM-->>Coord: conclusion {root_cause, confidence}
    end

    alt P1 severity (approval required)
        Coord->>Discord: Approval required embed
        Operator->>App: POST /approve (API key, token, decision)
        App->>Coord: approval callback
    end

    alt Explicitly approved (P1) or automatic P2/P3
        rect rgb(30,40,60)
            Note over Coord,LLM: Remediation Agent
            loop One bounded mutation per decision
                Coord->>LLM: chat/completions (anchors + provenance + environment observations)
                LLM-->>Coord: one proposed tool call
                Coord->>Coord: ACL + evidence-precondition validation
                Coord->>Tools: one mutating tool call
                Tools-->>Coord: tool result
                Coord->>Coord: authoritative verifier observation
                Coord-->>LLM: structured action + verifier observation
            end
            Coord-->>LLM: conclusion {outcome: resolved/unresolved/escalated}
        end
    else Rejected or timed out (P1)
        Note over Coord: Skip remediation; record approval outcome and block execution
    end

    rect rgb(30,40,60)
        Note over Coord,LLM: Comms Agent
        Coord->>LLM: chat/completions (comms prompt)
        LLM-->>Coord: tool_call: slack_post_update, postmortem_draft
        Coord->>Discord: Closure embed (if webhook configured)
    end

    Coord->>CB: finish_incident(resolved, reason)
    Note over CB: approval_rejected does NOT trip breaker
    Coord->>Discord: Scenario run complete ping (finally block)
    Coord-->>App: full_record
```

## Key Design Decisions

**Webhook ingestion** — The authenticated Alertmanager payload enters the correlator.
The retired `/inject` path cannot synthesize an alert or bypass incident deduplication.

**Circuit breaker semantics** — Only `system_error` and `agent_error` outcomes count toward the consecutive failure threshold. Designed outcomes like `approval_rejected`, `manual_runbook`, and `approval_timeout` do not trip the breaker, so judges can reject remediation freely without locking the system. The hourly action quota applies only to cluster-mutating remediation tools; external communications and local postmortem writes remain subject to the general per-incident call limit but do not consume cluster-mutation capacity.

**HTTP retry on LLM calls** — The coordinator retries `chat/completions` on HTTP 429 (HF Router rate limit) and 5xx with exponential backoff, preventing transient inference hiccups from failing entire scenarios.

**Alert-anchor and provenance contract** — The incoming alert's primary service,
namespace, alert name, labels, and description remain explicit downstream. Triage
may add correlated services, but an unsupported target switch becomes an explicit
review/escalation state. Diagnosis and Remediation receive the original operational
context plus structured tool observations; evaluation-only scenario metadata is
removed before model serialization.

**Evidence semantics** — Prometheus transport success is separate from metric
evidence. A successful empty vector is reported as `no_series`, while a non-empty
vector is `series_present`. Active supported Chaos Mesh resources are exposed by a
read-only generic observation tool.

**Remediation action boundary** — Mutating actions are serialized one at a time.
After each action, the runtime records the tool result and invokes the objective
environment verifier before another mutation can execute. Known-terminal errors
such as authorization failure or invalid revision block cosmetic retries.

**Web action boundary** — `/inject` and `/reset` return 503 even with an API key
and old opt-in flags; they cannot meet the governed preflight, alert-causality,
cleanup, and verifier contract. `/approve` and the pending-approval endpoint
require `ATLASOPS_API_KEY`. The Alertmanager `/webhook` requires
`ALERTMANAGER_WEBHOOK_SECRET`; an unsigned webhook cannot start an incident.
Real fault injection and verified cleanup belong to the Stage 4 harness.
