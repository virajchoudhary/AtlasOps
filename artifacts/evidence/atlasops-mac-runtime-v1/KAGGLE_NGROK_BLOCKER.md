# Phase 1 (Kaggle + Ollama + authenticated ngrok): BLOCKED, not attempted

Checked 29 October 2026 on the Mac, before any attempt. The brief authorises this
route "only if current platform terms permit it and authorized credentials are
available", and requires the exact blocker on record otherwise. Two independent
conditions fail, so nothing was attempted and no Kaggle claim is made.

## 1. No authorised ngrok credential on this machine

| Check | Result |
|---|---|
| `~/.config/ngrok/ngrok.yml` | absent |
| `~/Library/Application Support/ngrok/ngrok.yml` | absent |
| `ngrok` binary | not installed |
| `~/.kaggle/kaggle.json` | present, 71 bytes, mode 600 (API token only) |
| `kaggle` CLI | not installed |

The run plan (section 6.1) requires two Kaggle notebook secrets, `NGROK_AUTHTOKEN`
and `BUG_LAB_LLM_API_KEY`. No ngrok authtoken exists here. A Kaggle API token is
not an ngrok credential and does not establish that the account has the verified
phone number that GPU and internet access require. No credential was created,
requested or guessed.

## 2. Platform terms do not demonstrably permit it

- `https://www.kaggle.com/terms` could not be read from this machine: the page is
  client-rendered and returned only its title, so no clause was quoted either way.
  This is recorded as "not verified", not as "permitted".
- No official Kaggle statement permitting tunnels from notebooks was found. The
  run plan reached the same conclusion on 27 October (section 6.5: "We found no
  official statement about tunnels") and listed "Uncertainty about Kaggle's terms"
  as an open risk.
- Kaggle product-feedback thread 743895 reports notebook LLM tunnels being caught
  by an abuse filter and asks for outbound tunnels to be blocked from GPU
  sessions. That is one user's claim and not confirmed enforcement, but it is
  evidence against, not for, permission.

Absence of a prohibition is not permission. The condition in the brief is that the
terms *permit* it; that was not established.

## What is used instead

The previously working local Mac model is retained and is the backend in use:

```
backend.label = "qwen2.5:7b-instruct via http://host.docker.internal:8000/v1"
GET http://127.0.0.1:8000/v1/models without a key -> 401
```

Served by the local `buglab-llm-proxy` container on the Mac's loopback. No public
tunnel, no external exposure, no third-party compute, no spend.

## Status

**Phase 1: BLOCKED.** Phase 3 (Amazon EKS) remains out of scope and unauthorised;
it costs money and the brief forbids paid cloud resources. The Kaggle path is NOT
claimed to pass, and no Kaggle evidence exists because nothing was run.
