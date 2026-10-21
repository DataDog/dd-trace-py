# AI Guard through the Agent EVP proxy (POC)

Checks that the tracer can reach `POST /api/v2/ai-guard/evaluate` through the Datadog Agent
EVP proxy and get the backend response back, with no Datadog credential on the service.

When `DD_API_KEY` or `DD_APP_KEY` is missing, `new_ai_guard_client()` queries the Agent `/info`.
If the Agent advertises `/evp_proxy/v4/` (or `/evp_proxy/v2/`), the client sends evaluations to
`<agent>/evp_proxy/v4/api/v2/ai-guard/evaluate` with `X-Datadog-EVP-Subdomain: api`. The Agent
forwards them to `api.<site>` with its own API key and streams the response back.

## Setup

Create `.env` next to this file (gitignored). Only the Agents get these values:

```
DD_API_KEY=...
DD_APP_KEY=...        # only used by the Agent 7.80 scenario
DD_SITE=datadoghq.com
```

## Scenarios

Path 1, Agent with an API key only (what the backend would need to accept):

```
docker compose up -d agent
docker compose run --rm app
```

Until the evaluate route accepts API-key-only auth, this returns the backend's 401/403,
which still shows the request and response go through the proxy.

Path 2, Agent attaches its own app key (`X-Datadog-NeedsAppKey`, removed in Agent 7.81):

```
docker compose up -d agent-app-key
docker compose run --rm app-app-key
```

This should return real verdicts with the current backend.

Clean up with `docker compose down`.

## Known gaps

- The EVP proxy drops the `DD-AI-GUARD-VERSION`, `DD-AI-GUARD-SOURCE` and `DD-AI-GUARD-LANGUAGE` headers.
- Agent discovery runs once, synchronously, when the client is created.
- Unix socket Agent URLs are not handled by the AI Guard HTTP transport.
