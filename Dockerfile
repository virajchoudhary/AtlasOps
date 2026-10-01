FROM python:3.11-slim

ARG KUBECTL_VERSION=v1.31.10
ARG KUBECTL_SHA256=f7e806b676bea3b4995e9c236445a5f24ae61ed3d5245c39d7b816d209b06a78

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    curl ca-certificates && \
    # Install kubectl
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/amd64/kubectl" \
        -o /usr/local/bin/kubectl && \
    echo "${KUBECTL_SHA256}  /usr/local/bin/kubectl" | sha256sum -c - && \
    chmod +x /usr/local/bin/kubectl && \
    rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir . "uvicorn[standard]"

# HF Spaces runs as user 1000 — ensure data dirs are writable
RUN mkdir -p data docs/postmortems && chmod -R 777 data docs

# HF Spaces port
EXPOSE 7860

# ── HF Space Secrets (minimal — see docs/HF_SPACE_SETUP.md) ───────────────────
#   HF_TOKEN=<read + inference capable>
#   ATLASOPS_USE_HF_INFERENCE=1
#   AGENT_MODEL=your-org/merged-atlasops-7b-grpo      # Hub id after merging LoRA
#   JUDGE_MODEL=Qwen/Qwen2.5-72B-Instruct-AWQ         # or a smaller HF id Router allows
# Optional: ATLASOPS_LIVE_JUDGE=1|0                     (defaults ON when inference pack enabled)
#
# Comms out (optional):
#   DISCORD_WEBHOOK_URL   # Server Settings → Integrations → Webhooks → channel URL
#   SLACK_WEBHOOK_URL

# Existing cluster / Grafana wiring:
#   PROMETHEUS_URL, ALERTMANAGER_URL, JAEGER_URL, GRAFANA_URL, ARGOCD_URL, BOUTIQUE_URL
#   ATLASOPS_API_KEY, ALERTMANAGER_WEBHOOK_SECRET
# Web fault injection/reset are retired; flags cannot enable them.

CMD ["python", "app.py"]
