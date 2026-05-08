import json

import pytest

from ddtrace.internal.utils.http import Response
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._memory import DatadogMemoryBackend
from ddtrace.llmobs._memory import EmptyMemoryBackend
from ddtrace.llmobs._memory import MEMORY_GET_TOOL_SCHEMA
from ddtrace.llmobs._memory import MEMORY_PERSIST_TOOL_SCHEMA
from ddtrace.llmobs._memory import MEMORY_SEARCH_TOOL_SCHEMA
from ddtrace.llmobs._memory import Memory
from ddtrace.llmobs._memory import MemoryBackendError
from ddtrace.llmobs._memory import MemoryLink
from ddtrace.llmobs._memory import MemoryNotConfiguredError
from ddtrace.llmobs._memory import MemoryPersistUnsupportedError
from ddtrace.llmobs._memory import MemoryRecord
from ddtrace.llmobs._memory import MemoryScope
from ddtrace.llmobs._memory import MemorySource
from ddtrace.llmobs._memory import get_memory_scope
from ddtrace.llmobs._memory import memory_scope
from ddtrace.llmobs._utils import get_llmobs_input_value
from ddtrace.llmobs._utils import get_llmobs_metadata
from ddtrace.llmobs._utils import get_llmobs_metrics
from ddtrace.llmobs._utils import get_llmobs_output_value
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_span_name
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs._utils import get_llmobs_trace_id
from tests.utils import override_global_config


def test_public_memory_imports():
    from ddtrace.llmobs import Memory as TopLevelMemory
    from ddtrace.llmobs import MemoryScope as TopLevelMemoryScope
    from ddtrace.llmobs.memory import Memory as ModuleMemory
    from ddtrace.llmobs.memory import MemoryScope as ModuleMemoryScope

    assert TopLevelMemory is ModuleMemory
    assert TopLevelMemoryScope is ModuleMemoryScope


class RecordingMemoryBackend:
    def __init__(self):
        self.search_calls = []
        self.get_calls = []
        self.persist_calls = []
        self.get_record = None
        self.search_records = [
            MemoryRecord(
                id="mem_1",
                content="User prefers green tea in the afternoon.",
                links=(MemoryLink(type="source", target_id="artifact_1", token_count=12),),
            )
        ]

    def search(self, scope, query, limit):
        self.search_calls.append((scope, query, limit))
        return self.search_records

    def get(self, scope, id):
        self.get_calls.append((scope, id))
        return self.get_record

    def persist(self, scope, content, source):
        self.persist_calls.append((scope, content, source))
        return MemoryRecord(id="mem_new", content=content, source=source, state="active")


class FakeMemoryClient:
    def __init__(self, *responses, api_key="api", app_key="app"):
        self._api_key = api_key
        self._app_key = app_key
        self.responses = list(responses)
        self.requests = []

    def request(self, method, path, body):
        self.requests.append((method, path, body))
        return self.responses.pop(0)


def _response(status, body):
    return Response(status=status, body=json.dumps(body))


def _llmobs_spans(test_spans):
    return [span for trace in test_spans.pop_traces() for span in trace if get_llmobs_span_kind(span)]


def _span_by_name(spans, name):
    return next(span for span in spans if get_llmobs_span_name(span) == name)


def test_memory_scope_validation_and_context():
    scope = MemoryScope(kind="user", id="user_1")
    assert scope.to_wire() == "user:user_1"
    assert get_memory_scope() is None
    with memory_scope(scope):
        assert get_memory_scope() == scope
        assert Memory().scope == scope
    assert get_memory_scope() is None
    with pytest.raises(ValueError, match="requires a scope"):
        Memory()
    with pytest.raises(ValueError, match="kind"):
        MemoryScope(kind="", id="user_1")
    with pytest.raises(ValueError, match="id"):
        MemoryScope(kind="user", id="")


def test_memory_search_get_and_persist_validate_and_call_backend_without_llmobs():
    backend = RecordingMemoryBackend()
    memory = Memory(scope=MemoryScope(kind="user", id="user_1"), backend=backend, default_limit=7)

    assert json.loads(memory.search("tea", limit=0))[0]["id"] == "mem_1"
    assert backend.search_calls == [(memory.scope, "tea", 7)]

    assert json.loads(memory.get("missing")) == {"result": "not_found", "id": "missing"}
    assert backend.get_calls == [(memory.scope, "missing")]

    persisted = json.loads(memory.persist("User prefers washable cotton bedding."))
    assert persisted["id"] == "mem_new"
    assert backend.persist_calls[0][2] == MemorySource(type="agent", agent={})

    with pytest.raises(ValueError, match="query is required"):
        memory.search("")
    with pytest.raises(ValueError, match="id is required"):
        memory.get("")
    with pytest.raises(ValueError, match="content is required"):
        memory.persist("")
    with pytest.raises(ValueError, match="positive integer"):
        memory.search("tea", limit=-1)


def test_empty_memory_backend_persist_is_explicitly_unsupported():
    memory = Memory(scope=MemoryScope(kind="user", id="user_1"), backend=EmptyMemoryBackend())
    with pytest.raises(MemoryPersistUnsupportedError):
        memory.persist("User likes tea.")


def test_tool_schemas_do_not_expose_scope():
    for schema in (MEMORY_SEARCH_TOOL_SCHEMA, MEMORY_GET_TOOL_SCHEMA, MEMORY_PERSIST_TOOL_SCHEMA):
        assert "scope" not in schema["parameters"]["properties"]
        assert schema["parameters"]["additionalProperties"] is False
    assert MEMORY_SEARCH_TOOL_SCHEMA["parameters"]["required"] == ["query"]
    assert MEMORY_GET_TOOL_SCHEMA["parameters"]["required"] == ["id"]
    assert MEMORY_PERSIST_TOOL_SCHEMA["parameters"]["required"] == ["content"]


def test_datadog_memory_backend_search_maps_memory_links():
    client = FakeMemoryClient(
        _response(
            200,
            {
                "data": [
                    {
                        "id": "mem_1",
                        "kind": "memory",
                        "content": "User likes tea.",
                        "memory_links": [{"type": "source", "target_id": "artifact_1", "token_count": 11}],
                        "created_at": "2026-05-08T00:00:00Z",
                        "metadata": {"rank": 1},
                    }
                ]
            },
        )
    )

    records = DatadogMemoryBackend(client=client, ml_app="shopping").search(MemoryScope("user", "user_1"), "tea", 5)

    assert client.requests[0][1] == "/api/unstable/agent_memories/agent/search"
    assert client.requests[0][2] == {"ml_app": "shopping", "scope": "user:user_1", "query": "tea", "limit": 5}
    assert records[0].links == (MemoryLink(type="source", target_id="artifact_1", token_count=11),)
    assert records[0].to_json()["links"][0]["target_id"] == "artifact_1"


def test_datadog_memory_backend_get_404_returns_none():
    client = FakeMemoryClient(Response(status=404, body=b'{"errors":["node not found"]}'))

    assert DatadogMemoryBackend(client=client, ml_app="shopping").get(MemoryScope("user", "user_1"), "mem_1") is None


def test_datadog_memory_backend_persist_parses_memory_record():
    source = MemorySource(type="agent", agent={"session_id": "sess_1", "trace_id": "trace_1", "span_id": "span_1"})
    client = FakeMemoryClient(
        _response(
            201,
            {
                "data": {
                    "id": "mem_2",
                    "content": "User likes tea.",
                    "state": "active",
                    "source": source.to_json(),
                    "created_at": "2026-05-08T00:00:00Z",
                    "updated_at": "2026-05-08T00:00:00Z",
                }
            },
        )
    )

    record = DatadogMemoryBackend(client=client, ml_app="shopping").persist(
        MemoryScope("user", "user_1"), "User likes tea.", source
    )

    assert client.requests[0][1] == "/api/unstable/agent_memories/agent/persist"
    assert client.requests[0][2]["source"] == source.to_json()
    assert record.state == "active"
    assert record.source == source


def test_datadog_memory_backend_errors_are_clear():
    with pytest.raises(MemoryNotConfiguredError, match="DD_APP_KEY"):
        DatadogMemoryBackend(client=FakeMemoryClient(api_key="api", app_key=""), ml_app="shopping").search(
            MemoryScope("user", "user_1"), "tea", 5
        )

    with pytest.raises(MemoryBackendError, match="status 500"):
        DatadogMemoryBackend(client=FakeMemoryClient(Response(status=500, body=b"boom")), ml_app="shopping").search(
            MemoryScope("user", "user_1"), "tea", 5
        )

    with pytest.raises(MemoryPersistUnsupportedError, match="does not support persistence"):
        DatadogMemoryBackend(
            client=FakeMemoryClient(Response(status=500, body=b"not implemented")), ml_app="shopping"
        ).persist(MemoryScope("user", "user_1"), "User likes tea.", MemorySource(type="agent", agent={}))


def test_memory_search_traces_tool_span(llmobs, test_spans):
    backend = RecordingMemoryBackend()
    memory = Memory(scope=MemoryScope(kind="user", id="user_1"), backend=backend)

    assert json.loads(memory.search("tea", limit=2))[0]["id"] == "mem_1"

    span = _span_by_name(_llmobs_spans(test_spans), "memory_search")
    assert get_llmobs_span_kind(span) == "tool"
    assert json.loads(get_llmobs_input_value(span)) == {"limit": 2, "query": "tea"}
    assert json.loads(get_llmobs_output_value(span))[0]["links"][0]["target_id"] == "artifact_1"
    assert get_llmobs_tags(span)["tool.kind"] == "memory"
    assert get_llmobs_tags(span)["memory.action"] == "search"
    assert get_llmobs_tags(span)["memory.scope"] == "user"
    metadata = get_llmobs_metadata(span)
    assert metadata["kind"] == "memory"
    assert metadata["memory.scope_id"] == "user_1"
    assert metadata["memory.query"] == "tea"
    assert metadata["memory.limit"] == 2
    assert metadata["memory.count"] == 1
    assert metadata["memory.ids"] == ["mem_1"]
    assert get_llmobs_metrics(span)["total_tokens"] > 0


def test_memory_get_traces_not_found(llmobs, test_spans):
    memory = Memory(scope=MemoryScope(kind="user", id="user_1"), backend=RecordingMemoryBackend())

    assert json.loads(memory.get("missing")) == {"result": "not_found", "id": "missing"}

    span = _span_by_name(_llmobs_spans(test_spans), "memory_get")
    assert json.loads(get_llmobs_input_value(span)) == {"id": "missing"}
    assert json.loads(get_llmobs_output_value(span)) == {"result": "not_found", "id": "missing"}
    metadata = get_llmobs_metadata(span)
    assert metadata["memory.id"] == "missing"
    assert metadata["memory.result"] == "not_found"
    assert metadata["memory.state"] == "not_found"
    assert metadata["memory.count"] == 0


def test_memory_persist_traces_source_provenance(llmobs, test_spans):
    backend = RecordingMemoryBackend()
    memory = Memory(scope=MemoryScope(kind="user", id="user_1"), backend=backend)

    with llmobs.workflow("parent", session_id="sess_1"):
        assert json.loads(memory.persist("User prefers washable cotton bedding."))["id"] == "mem_new"

    spans = _llmobs_spans(test_spans)
    span = _span_by_name(spans, "memory_persist")
    source = backend.persist_calls[0][2]
    assert source.agent["session_id"] == "sess_1"
    assert source.agent["trace_id"] == get_llmobs_trace_id(span)
    assert source.agent["span_id"] == str(span.span_id)
    assert json.loads(get_llmobs_input_value(span)) == {"content": "User prefers washable cotton bedding."}
    assert json.loads(get_llmobs_output_value(span))["state"] == "active"
    assert get_llmobs_tags(span)["memory.op"] == "write"
    assert get_llmobs_tags(span)["memory.action"] == "persist"
    metadata = get_llmobs_metadata(span)
    assert metadata["memory.id"] == "mem_new"
    assert metadata["memory.source"] == "agent"
    assert metadata["memory.state"] == "active"
    assert metadata["memory.source.session_id"] == "sess_1"
    assert metadata["memory.source.trace_id"] == get_llmobs_trace_id(span)
    assert metadata["memory.source.span_id"] == str(span.span_id)


def test_llmobs_memory_factory_requires_enabled_without_custom_backend():
    with pytest.raises(MemoryNotConfiguredError, match="requires LLMObs.enable"):
        llmobs_service.memory(scope=MemoryScope("user", "user_1"))

    memory = llmobs_service.memory(scope=MemoryScope("user", "user_1"), backend=RecordingMemoryBackend())
    assert isinstance(memory, Memory)


def test_llmobs_memory_factory_uses_service_memory_client(tracer):
    with override_global_config(dict(_dd_api_key="api-key", _dd_site="datadoghq.com", _llmobs_ml_app="shopping")):
        llmobs_service.enable(_tracer=tracer, integrations_enabled=False, app_key="app-key", agentless_enabled=True)
        try:
            memory = llmobs_service.memory(scope=MemoryScope("user", "user_1"))
            assert isinstance(memory.backend, DatadogMemoryBackend)
            assert memory.backend._client is llmobs_service._instance._memory_client
            assert memory.backend._client._api_key == "api-key"
            assert memory.backend._client._app_key == "app-key"
            assert memory.backend._ml_app == "shopping"
        finally:
            llmobs_service.disable()
