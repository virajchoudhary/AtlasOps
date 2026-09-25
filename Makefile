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

# ── Retired live shortcuts ────────────────────────────────────────────────────
.PHONY: chaos chaos-reset

chaos:
	@echo "Retired: use the governed Stage 4 harness after its complete preflight."
	@exit 2

chaos-reset:
	@echo "Retired: use scoped Stage 4 cleanup and verify the environment."
	@exit 2

replay-%:
	@echo "Retired: use the governed Stage 4 harness after its complete preflight."
	@exit 2

# ── Agent runtime ──────────────────────────────────────────────────────────────
.PHONY: coordinator

coordinator:
	python agents/coordinator.py

# ── Benchmark ─────────────────────────────────────────────────────────────────
.PHONY: bench bench-baseline

bench:
	python -m bench.runner --model $(or $(MODEL),fixture) --mock --adversarial 0

bench-baseline:
	python -m bench.runner --model fixture --tag baseline_fixture --mock --adversarial 0

# ── Training ───────────────────────────────────────────────────────────────────
.PHONY: sft grpo trajectories

trajectories:
	@echo "Retired: use the frozen Train-split corpus and Stage 7 reproducibility contract."
	@exit 2

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
