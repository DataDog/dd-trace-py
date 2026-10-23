# LLMObs integration tests

Use `scripts/run-tests`; see the `run-tests` skill for suite discovery and environment selection.
Tests live under `tests/contrib/<name>/`, with LLM/AI suite definitions in `tests/llmobs/suitespec.yml`.
Copy the current suite schema, including its matrix/variants; do not invent a top-level dependency list.

## Choose the existing transport

- Local vcrpy fixtures: read `tests/contrib/anthropic/utils.py` or
  `tests/contrib/botocore/bedrock_utils.py` for matching, cassette paths, and auth-header filtering.
- Test-agent VCR proxy: preserve the provider base URL, test naming, and CI replay configuration
  used by the suite. See [test-agent documentation](https://github.com/DataDog/dd-apm-test-agent#recording-3rd-party-api-requests)
  for the current proxy interface; do not assume it matches vcrpy.
- Mock clients/streams: use for deterministic error, cancellation, and agent-loop cases; see
  `tests/contrib/claude_agent_sdk/conftest.py`. Keep mock shapes aligned with the supported SDK.

Keep existing transport coverage when adding focused mocks. Replay with placeholder credentials.
Recording needs real credentials and may call the provider; verify filtering and inspect cassettes
before committing. Never record credentials into request headers or payloads.

## Assertions

Use `assert_llmobs_span_data` from `tests/llmobs/_utils.py` with
`_get_llmobs_data_metastruct(span)` from `ddtrace.llmobs._utils`.
Read `tests/contrib/anthropic/test_anthropic_llmobs.py` for actual fixtures and assertion signatures.
Prefer existing snapshot fixtures for APM span structure.

Cover the behavior the integration supports: request/response, streaming, tools, errors, multi-turn
messages, model extraction, sync/async, and usage (including cache tokens and billed server tools).
For lifecycle changes, include early exit, abandonment, cancellation, and cleanup errors as applicable.
For extraction changes, assert both preserved data and intentionally omitted fields.
