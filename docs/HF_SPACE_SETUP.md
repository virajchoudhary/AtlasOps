# Hugging Face Spaces — wired 7B agents + 72B judge

> **Historical hackathon setup notes.** These instructions do not establish a safe current deployment. The current FastAPI runtime requires `ATLASOPS_API_KEY` for approval actions and `ALERTMANAGER_WEBHOOK_SECRET` for `/webhook`. Web Chaos injection and cleanup are retired even with old opt-in flags. Do not deploy the old public incident UI or use this page as a G14 acceptance record.

## Hackathon Space URL (avoid 404)

The LabLab org Space slug uses a **hyphen**: **`atlas-ops`**, not `atlasops`.

| What | URL |
|------|-----|
| Space repo (clone / git push) | `https://huggingface.co/spaces/lablab-ai-amd-developer-hackathon/atlas-ops` |
| Embedded app (typical) | `https://lablab-ai-amd-developer-hackathon-atlas-ops.hf.space` |
| Health check | Same host as app + `/health` (not the repo `.git` URL) |

If you **duplicate or rename** the Space, replace `atlas-ops` everywhere below with your actual slug.

**Git remote example**

```bash
git remote remove lablab 2>/dev/null
git remote add lablab https://huggingface.co/spaces/lablab-ai-amd-developer-hackathon/atlas-ops
git push lablab main
```

**Secrets:** set **`ATLASOPS_PUBLIC_BASE_URL`** to the **app** URL (no trailing slash), e.g. `https://lablab-ai-amd-developer-hackathon-atlas-ops.hf.space`, so approval / Discord hints use the Space the judges open — not localhost and not an old slug.

---

AtlasOps speaks **two** OpenAI-compatible HTTP endpoints:

| Role | Env vars | Typical model id |
|------|-----------|-------------------|
| **Incident agents** (triage→comms) | `VLLM_BASE`, `AGENT_MODEL`, token | Your merged 7B on Hub **or** `Qwen/Qwen2.5-7B-Instruct` |
| **Judge** (scores responses; benchmarks + optional live ribbon) | `JUDGE_URL`, `JUDGE_MODEL`, token | Smaller HF model if 72B is blocked on quota |

## One-switch setup on the Space root

Configure **Space → Settings → Variables and secrets**:

1. **`HF_TOKEN`** — your HF access token (**read** plus **Inference** / **fine-grained Inference** permission if you use Router). **This is the minimum to un-block the agent strip** on a fresh Space: the app now **auto-detects** a Hugging Face Space container and, if `VLLM_BASE` / `JUDGE_URL` still look like `localhost`, **replaces them** with the HF Inference Router. You do **not** have to add `ATLASOPS_USE_HF_INFERENCE=1` for that rescue (but you can set it explicitly for clarity).
2. **`ATLASOPS_USE_HF_INFERENCE`** = `1` — optional if auto-routing already applied; set manually if you want the “HF pack” on every environment.

`config/hf_space_env.py` copies `HF_TOKEN` into `LLM_API_KEY` and `JUDGE_API_KEY`, and overwrites **loopback** `VLLM_BASE` / `JUDGE_URL` with **`https://router.huggingface.co/v1`**. If you **already** set a **public** `VLLM_BASE` to your MI300X, that URL is **left alone**.

**Opt out of auto-routing** (use only your self-hosted URL, even on a Space): `ATLASOPS_AUTO_HF_INFERENCE=0` or set a non-loopback `VLLM_BASE` first.

Optional override:

```
HF_INFERENCE_BASE=https://router.huggingface.co/v1
```

## Model IDs agents will call

Required:

```
AGENT_MODEL=<your-namespace/your-atlasops-merged-7b>
JUDGE_MODEL=Qwen/Qwen2.5-72B-Instruct-AWQ
```

(or any Hub model Router actually serves under your billing tier)

72B AWQ often needs a **paid** Inference allotment or **dedicated Inference Endpoint**.  
If Router returns 429/403 on 72B, set for example **`JUDGE_MODEL=Qwen/Qwen2.5-32B-Instruct`** temporarily — AtlasOps keeps working.

## Putting your GRPO weights on Hugging Face (7B)

The coordinator sends **only** a `model` string (no silent LoRA layer). Serving options:

1. **Merge LoRA locally** into the base checkpoint, upload the merged weights to `your-org/atlasops-7b-grpo`, set `AGENT_MODEL` to that repo (see `training/merge_lora_for_hub.py` after `pip install -e ".[train]"`).
2. **Self-hosted vLLM + `--enable-lora`** on AMD hardware (not HF Space CPU) — would require coordinator changes to attach LoRA per request unless you bake merged weights yourself.

For most hackathon demos **merged Hub model + Router** is the least painful.

## Live judge inside the Ops UI

When `ATLASOPS_USE_HF_INFERENCE=1`, the coordinator fires **one judge call after comms**, and the timeline prints the score (`judge_trajectory` tool line).

Explicit flags:

```
ATLASOPS_LIVE_JUDGE=1   # force ON
ATLASOPS_LIVE_JUDGE=0   # force OFF even with HF inference pack
```

Local MI300x with `JUDGE_URL=http://localhost:8001/v1`: keep **`ATLASOPS_USE_HF_INFERENCE` unset**, set **`ATLASOPS_LIVE_JUDGE=1`** if you still want judge lines in Grafana.

## Health checks after deploy

- `GET https://<space>/health` — agent + judge **model names** + bases (inspect JSON).
- `GET https://<space>/api/health` — coordinator copy with `live_judge` Boolean.

Neither endpoint prints raw tokens.

After redeploy with the bundled UI, the **footer bar** polls `/health` and shows **`Discord webhook ✓`** or **`✗ add DISCORD_WEBHOOK_URL`** so you know whether `DISCORD_WEBHOOK_URL` reached the Space (no webhook URL is ever rendered).

## Summary checklist

```
HF_TOKEN=<secret>
ATLASOPS_USE_HF_INFERENCE=1
AGENT_MODEL=your-org/your-trained-merged-7b
JUDGE_MODEL=Qwen/Qwen2.5-72B-Instruct-AWQ    # or a smaller HF model Router allows
BACKEND=openai                                 # optional; bootstrap sets default when ATLASOPS_USE_HF_INFERENCE=1
```

The original Space workflow above is historical. Review current authentication and Stage 4 governance before any deployment or live experiment.

## Current web incident boundary

`POST /inject` and `POST /reset` return 503 after operator authentication. Setting `ATLASOPS_ENABLE_WEB_CHAOS`, `ATLASOPS_WEB_CHAOS_CONTEXT`, or the old skip flag cannot enable them. The web shortcut lacks the Stage 4 preflight and cannot prove that a newly observed alert was caused by its fault or that cleanup restored the environment. `/approve` and `/approval/pending` require the API key, so a pending token cannot be obtained and used anonymously. `/webhook` requires `ALERTMANAGER_WEBHOOK_SECRET`.

These retired routes execute no command, start no incident, and clear no local state. Use the read-only Gradio console when no cluster action is intended.

The public HTML frontend has not been established as an authenticated operator console. Do not enable these web mutation routes on a public Space. For tomorrow's local presentation, use the read-only Gradio console described in `docs/project/STAGE_14_DEPLOY_FINAL_DEMO.md`. Run real G4 only through its governed harness after preflight and authorization.

---

## Discord (why nothing appears in your server)

AtlasOps does **not** use a Discord “bot” that shows online in the member list. It posts through an **Incoming Webhook** URL.

1. Discord → your server → **Server Settings** → **Integrations** → **Webhooks** → **New Webhook** → pick `#general` (or a channel) → copy **Webhook URL**.
2. In HF Space **Secrets**, add **`DISCORD_WEBHOOK_URL`** = that URL (same as `agents/tools/comms.py` expects).
3. **Every scenario/incident pipeline** triggers **one automatic Discord embed** when `DISCORD_WEBHOOK_URL` is set (coordinator fires this in `finally` after each `handle_incident`, including demo injects — independent of whether the LLM calls `slack_post_update`). Approval + closure + this ping can **burst** Discord’s webhook quota (**HTTP 429**); delivery now **retries** with `Retry-After`. Disable noise with **`ATLASOPS_DISCORD_EVERY_RUN_PING=0`**. Extra detail still appears when **comms** uses `slack_post_update`. If Discord is still empty after a run finishes, check Space logs and **`VLLM_BASE`** (stalled pipelines never reach `finally`).

Optional: **`SLACK_WEBHOOK_URL`** for Slack in parallel; both can be set.

---

## Historical submission checklist

The former public Space checklist was written for the upstream hackathon and is not a current deployment procedure. A safe new deployment needs an authenticated operator UI, a configured API key and webhook secret, a governed live experiment path with preflight and verified cleanup, and the stage-specific evidence required by the Master Pipeline. The local read-only demo does not require those live prerequisites and does not claim the historical Space is current.

