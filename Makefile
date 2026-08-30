REGION      ?= us-central1
CLUSTER     ?= atlasops
ZONE        ?= $(REGION)-a
KUBE_CONTEXT ?= gke_$(PROJECT)_$(ZONE)_$(CLUSTER)

# ── Cluster lifecycle ──────────────────────────────────────────────────────────
.PHONY: require-project require-kube-context infra-check teardown-check up down status

require-project:
	@if [ -z "$(strip $(PROJECT))" ]; then echo "ERROR: PROJECT is required. Pass PROJECT=<gcp-project-id>."; exit 2; fi

require-kube-context: require-project
	@if [ -z "$(strip $(KUBE_CONTEXT))" ]; then echo "ERROR: KUBE_CONTEXT is required."; exit 2; fi

infra-check: require-project
	ATLASOPS_GKE_ZONE=$(ZONE) bash infra/setup.sh $(PROJECT) $(REGION) $(CLUSTER) --check

teardown-check: require-project
	ATLASOPS_GKE_ZONE=$(ZONE) bash infra/teardown.sh $(PROJECT) $(REGION) $(CLUSTER) --check

up: require-project
	@if [ "$(APPLY)" != "true" ]; then echo "Refusing: use make up APPLY=true plus the required setup environment gates."; exit 2; fi
	ATLASOPS_GKE_ZONE=$(ZONE) bash infra/setup.sh $(PROJECT) $(REGION) $(CLUSTER) --apply

down: require-project
	@if [ "$(APPLY)" != "true" ]; then echo "Refusing: use make down APPLY=true plus ATLASOPS_TEARDOWN_ACK."; exit 2; fi
	ATLASOPS_GKE_ZONE=$(ZONE) bash infra/teardown.sh $(PROJECT) $(REGION) $(CLUSTER) --apply

status: require-kube-context
	kubectl --context="$(KUBE_CONTEXT)" get pods -A

# ── Chaos injection ────────────────────────────────────────────────────────────
.PHONY: chaos chaos-reset

chaos: require-kube-context
	@if [ -z "$(SCENARIO)" ]; then echo "Usage: make chaos SCENARIO=sf-001"; exit 1; fi
	@MANIFEST=$$(find bench/chaos_manifests -name "$(SCENARIO).yaml" | head -1); \
	if [ -z "$$MANIFEST" ]; then echo "Scenario $(SCENARIO) not found"; exit 1; fi; \
	echo "Applying chaos: $$MANIFEST"; \
	kubectl --context="$(KUBE_CONTEXT)" apply -f "$$MANIFEST"

chaos-reset: require-kube-context
	kubectl --context="$(KUBE_CONTEXT)" delete podchaos,networkchaos,stresschaos,dnschaos,iochaos,timechaos --all -A --ignore-not-found=true

# ── Historical replays ─────────────────────────────────────────────────────────
replay-%: require-kube-context
	kubectl --context="$(KUBE_CONTEXT)" apply -f bench/chaos_manifests/named_replays/$*.yaml
	@echo "Replay $* triggered. Watch: make status"

# ── Agent runtime ──────────────────────────────────────────────────────────────
.PHONY: coordinator

coordinator:
	python agents/coordinator.py

# ── Benchmark ─────────────────────────────────────────────────────────────────
.PHONY: bench bench-baseline

bench:
	python bench/runner.py --model $(MODEL) --output bench/results/$(shell date +%Y%m%d_%H%M%S)

bench-baseline:
	python bench/runner.py --model checkpoints/cloudsre_v2_baseline --tag baseline_v2 \
	  --output bench/results/baseline_v2

# ── Training ───────────────────────────────────────────────────────────────────
.PHONY: sft grpo trajectories

trajectories:
	python training/generate_trajectories.py --output data/sft_corpus.jsonl

sft:
	python training/sft.py \
	  --model Qwen/Qwen2.5-7B-Instruct \
	  --data data/sft_corpus.jsonl \
	  --output checkpoints/sft_v3

grpo:
	python training/grpo.py \
	  --model checkpoints/sft_v3 \
	  --output checkpoints/grpo_v3 \
	  --tiers cascade,multi_fault,named_replays

# ── Dashboard ─────────────────────────────────────────────────────────────────
.PHONY: dashboard

dashboard:
	python dashboard.py

# ── Linting / tests ───────────────────────────────────────────────────────────
.PHONY: lint test release-gate smoke-e2e-local

lint:
	ruff check .

test:
	pytest tests/ -v

release-gate:
	python scripts/release_gate.py --strict --output docs/RELEASE_READINESS.md

smoke-e2e-local:
	pytest tests/test_app_endpoints.py tests/test_coordinator.py tests/test_tools.py tests/test_bench_runner.py -q

# ── No cluster, no GPU required ───────────────────────────────────────────────

rs-demo:                      ## Runbook recommender on the synthetic fixture
	python scripts/demo_runbook_recommender.py

probe-remediation:            ## Does the model reach the G4 goal state? (needs Ollama)
	python scripts/probe_remediation_behaviour.py

probe-remediation-control:    ## Negative control: no chaos experiment present
	python scripts/probe_remediation_behaviour.py --no-chaos

verify:                       ## Everything a reviewer can check without infrastructure
	python -m pytest tests/ -q
	ruff check . --select E9,F63,F7,F821
	python scripts/release_gate.py
	python scripts/demo_runbook_recommender.py

mutation-sweep:               ## Revert each repair in a sandbox; confirm a test catches it
	python scripts/mutation_sweep.py --control
	python scripts/mutation_sweep.py
