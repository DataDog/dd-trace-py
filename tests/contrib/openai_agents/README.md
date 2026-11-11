# OpenAI Agents Realtime validation

Run through `scripts/run-tests`, discovering hashes from the actual files first:

```sh
scripts/run-tests --list tests/contrib/openai_agents/test_openai_agents_realtime.py
scripts/run-tests --venv <returned-hash> -- -q -k realtime
```

The exact-version rows cover the first WebSocket release (Agents 0.2.0, OpenAI
1.93.1, websockets 14.0), first call attachment release (Agents 0.5.0, OpenAI
2.7.1, websockets 14.0), the separate conditional send path (Agents 0.19.3),
and Agents 0.24.0. Earlier ordinary Agents rows remain unchanged. The older
Realtime releases require the optional `websockets` dependency explicitly.

`test_openai_agents_realtime.py` runs the real WebSocket model and a real
`RealtimeRunner` against a fake socket. It checks normalized emitted payloads,
SDK-owned retrieval ordering, tool execution, raw event visibility, cleanup,
resource bounds, stored audio, and playback timing. Fixtures contain no provider
credentials or live caller recordings.

For a live smoke run, install the integration's current dependencies, configure
normal LLM Observability authentication and `OPENAI_API_KEY` in the environment,
and run:

```sh
ddtrace-run python tests/contrib/openai_agents/realtime_smoke.py
# Or attach to a call which an existing application has already accepted:
ddtrace-run python tests/contrib/openai_agents/realtime_smoke.py --call-id <call-id>
```

Use one application/tool owner for the call. The harness does not accept calls,
change webhooks, or provision telephone numbers. Provider tracing is disabled,
so all Datadog spans must come from automatic instrumentation. Stop with Ctrl-C.
In the resulting conversation, check transcripts, audio playback, model/provider,
per-response usage, matching tool call/result IDs, and speech windows. An
interruption case should preserve only the acknowledged audio prefix. A replay
passing locally does not establish live-provider playback behavior or backend
pricing; record those acceptance results separately without committing private
captures.

The standalone `benchmarks/openai_agents_realtime` scenario measures automatic
import/patch startup and a real SDK raw-receive boundary against a fake socket:

```sh
scripts/run-benchmarks --scenario openai_agents_realtime --baseline DataDog/dd-trace-py@<base-sha> --artifacts /tmp/agents-realtime-benchmarks
```

It is not registered as a CI performance gate until stable baseline measurements
can establish team-owned SLO thresholds. It does not measure audio conversion,
retrieval latency, or exporter memory.
