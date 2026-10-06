export type Nullable<T> = T | null;

export interface EvidenceReference {
  title: string;
  path: string;
  classification: string;
  area: string;
  available: boolean;
  sha256: Nullable<string>;
}

export interface EvidenceBrowserEntry {
  id: string;
  title: string;
  path: Nullable<string>;
  classification: string;
  area: string;
  available: boolean;
  sha256: Nullable<string>;
  scope: "current" | "historical";
  kind: "empirical" | "provenance" | "non-empirical" | "disposition";
  negative: boolean;
  availability: "present" | "external" | "missing";
  details: string;
}

export interface Gate {
  stage: string;
  name: string;
  gate: string;
  deliverable: string;
  status: string;
  status_note: string;
}

export interface AttemptSummary {
  name: string;
  id: string;
  scenario: string;
  timestamp: string;
  state: string;
  classification: string;
}

export interface FinalAttempt {
  attempt: string;
  state: string;
  classification: string;
  raw_record_available: boolean;
  source: EvidenceReference;
  source_note: string;
  resolution: Nullable<boolean>;
  reward: Nullable<number>;
  time_to_resolve_s: Nullable<number>;
}

export interface SftResult {
  available: boolean;
  status: Nullable<string>;
  run_id: Nullable<string>;
  model: Nullable<string>;
  model_revision: Nullable<string>;
  training_steps: Nullable<number>;
  corpus_rows: Nullable<number>;
  corpus_split: Nullable<string>;
  synthetic_corpus: Nullable<boolean>;
  adapter_sha256: Nullable<string>;
  reload_status: Nullable<string>;
  reload_tensor_count: Nullable<number>;
  incident_improvement_claim: boolean;
  evidence: EvidenceReference[];
}

export interface BaseSftResult {
  available: boolean;
  run_id: Nullable<string>;
  scope: Nullable<string>;
  base_f1: Nullable<number>;
  sft_f1: Nullable<number>;
  paired_delta: Nullable<number>;
  base_schema_valid: Nullable<number>;
  base_schema_total: Nullable<number>;
  sft_schema_valid: Nullable<number>;
  sft_schema_total: Nullable<number>;
  resolution_evaluated: Nullable<boolean>;
  resolution_rate: Nullable<number>;
  safety_result: Nullable<boolean | string>;
  avg_reward: Nullable<number>;
  avg_time_to_resolve_s: Nullable<number>;
  evidence: EvidenceReference[];
}

export interface G9PilotResult {
  available: boolean;
  status: Nullable<string>;
  optimizer_steps: Nullable<number>;
  completions: Nullable<number>;
  malformed_or_blocked: Nullable<number>;
  reward_each: Nullable<number>;
  zero_advantage_groups: Nullable<number>;
  reward_driven_advantage_groups: Nullable<number>;
  acceptable_checkpoint: Nullable<boolean>;
  archive_sha256: Nullable<string>;
  evidence: EvidenceReference[];
}

export interface G9AlignedDiagnostic {
  available: boolean;
  sample_count: Nullable<number>;
  admissible_count: Nullable<number>;
  optimizer_steps: Nullable<number>;
  tensor_hash_count: Nullable<number>;
  tensor_hashes_unchanged: Nullable<boolean>;
  model_mutated: Nullable<boolean>;
  population_probability: Nullable<number>;
  evidence: EvidenceReference[];
}

export interface CurrentResults {
  sft_v17: SftResult;
  base_vs_sft: BaseSftResult;
  g9_pilot: G9PilotResult;
  g9_aligned_diagnostic: G9AlignedDiagnostic;
  g4_attempts: FinalAttempt[];
}

export interface HistoricalArchive {
  title: string;
  path: string;
  classification: string;
  details: string;
}

export interface Runbook {
  id: string;
  title: string;
  category: string;
  description: string;
  tools: string[];
}

export interface Agent {
  id: string;
  name: string;
  role: string;
  purpose: string;
  input: string;
  output: string;
  tool_categories: string[];
  allowed_tools: string[];
  tool_count: Nullable<number>;
  governance_boundary: string;
}

export interface ProductControl {
  id: string;
  name: string;
  purpose: string;
}

export interface ProductBoundary {
  id: string;
  label: string;
  state: string;
}

export interface Product {
  name: string;
  subtitle: string;
  description: string;
  workstreams: string[];
  agent_count: Nullable<number>;
  tool_count: Nullable<number>;
  agent_exposed_tool_count: Nullable<number>;
  unexposed_tool_count: Nullable<number>;
  scenario_count: Nullable<number>;
  certification: string;
  presentation_status: string;
  model_method: string;
  agents: Agent[];
  controls: ProductControl[];
  boundaries: ProductBoundary[];
}

export interface Catalog {
  source: string;
  source_sha: Nullable<string>;
  gates: Gate[];
  attempts: AttemptSummary[];
  current_results: CurrentResults;
  historical_archive: HistoricalArchive[];
  runbooks: Runbook[];
  evidence: EvidenceReference[];
  evidence_browser?: EvidenceBrowserEntry[];
  product?: Product;
}

export interface AttemptDetail {
  name: string;
  id: string;
  classification: string;
  governance: string;
  state: string;
  timestamp: string;
  scenario: string;
  model: string;
  source: string;
  sha256: string;
  triage: {
    title: string;
    severity: string;
    services: string[];
    frozen_targets: string[];
  };
  diagnosis: {
    category: string;
    specific: string;
    confidence: Nullable<number>;
  };
  recommendation: string;
  approval: string;
  actions: { tool: string; target: string; success: boolean }[];
  verification: {
    env_resolved: Nullable<boolean>;
    status: string;
    checks: { name: string; target: string; details: string; passed: boolean; required: boolean }[];
  };
  warnings: string[];
}
