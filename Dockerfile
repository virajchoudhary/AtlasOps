FROM node:22-bookworm-slim AS frontend-build

WORKDIR /build/frontend

COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

COPY frontend/ ./
RUN npm run build

FROM python:3.11-slim

ARG TARGETARCH
ARG KUBECTL_VERSION=v1.35.8
ARG KUBECTL_SHA256=874d5e72dbb819f43cff16bcd1e4f8bac5b7f2361fe1e55049b0a6c676fb0cbf
ARG KUBECTL_SHA256_ARM64=cc749967b62f4422260bc9c0aa7a7c55f45175ae38cb8d95767b5d2b7e04c1fd
ARG ATLASOPS_SOURCE_SHA=unknown

ENV ATLASOPS_SOURCE_SHA=${ATLASOPS_SOURCE_SHA} \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/tmp

WORKDIR /app

LABEL org.opencontainers.image.source="https://github.com/virajchoudhary/AtlasOps" \
      org.opencontainers.image.revision="${ATLASOPS_SOURCE_SHA}"

RUN set -eu; \
    apt-get update; \
    apt-get install -y --no-install-recommends ca-certificates curl; \
    case "${TARGETARCH}" in \
      amd64) kubectl_sha256="${KUBECTL_SHA256}" ;; \
      arm64) kubectl_sha256="${KUBECTL_SHA256_ARM64}" ;; \
      *) echo "Unsupported kubectl architecture: ${TARGETARCH}" >&2; exit 1 ;; \
    esac; \
    curl -fsSL "https://dl.k8s.io/release/${KUBECTL_VERSION}/bin/linux/${TARGETARCH}/kubectl" \
        -o /usr/local/bin/kubectl; \
    echo "${kubectl_sha256}  /usr/local/bin/kubectl" | sha256sum -c -; \
    chmod 0755 /usr/local/bin/kubectl; \
    rm -rf /var/lib/apt/lists/*

COPY . .
RUN pip install --no-cache-dir . "uvicorn[standard]"
COPY --from=frontend-build /build/frontend/dist ./frontend/dist

RUN addgroup --system --gid 10001 atlasops \
    && adduser --system --uid 10001 --ingroup atlasops --home /home/atlasops atlasops \
    && mkdir -p /var/lib/atlasops/audit /var/lib/atlasops/comms \
        /var/lib/atlasops/postmortems /var/lib/atlasops/trajectories \
    && chown -R atlasops:atlasops /var/lib/atlasops /home/atlasops

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

USER 10001:10001

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "7860", "--workers", "1"]
