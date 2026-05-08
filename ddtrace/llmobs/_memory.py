from __future__ import annotations

from contextlib import contextmanager
from contextvars import ContextVar
from contextvars import Token
from dataclasses import dataclass
import json
from typing import Any
from typing import Iterator
from typing import Optional
from typing import Protocol
from typing import Union

from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.internal.utils.http import Response
from ddtrace.llmobs._constants import MEMORY_GET_ENDPOINT
from ddtrace.llmobs._constants import MEMORY_PERSIST_ENDPOINT
from ddtrace.llmobs._constants import MEMORY_SEARCH_ENDPOINT
from ddtrace.llmobs._utils import get_llmobs_session_id
from ddtrace.llmobs._utils import get_llmobs_trace_id
from ddtrace.llmobs._utils import resolve_ml_app


MEMORY_KIND = "memory"
BACKEND_INTERNAL = "internal"
OP_READ = "read"
OP_WRITE = "write"
ACTION_SEARCH = "search"
ACTION_GET = "get"
ACTION_PERSIST = "persist"
RESULT_NOT_FOUND = "not_found"

MEMORY_TOOL_SEARCH_NAME = "memory_search"
MEMORY_TOOL_GET_NAME = "memory_get"
MEMORY_TOOL_PERSIST_NAME = "memory_persist"


class MemoryBackendError(Exception):
    """Raised when a memory backend request fails."""


class MemoryNotConfiguredError(MemoryBackendError):
    """Raised when the Datadog memory backend is missing required configuration."""


class MemoryPersistUnsupportedError(MemoryBackendError):
    """Raised when the configured backend does not support memory persistence yet."""


@dataclass(frozen=True)
class MemoryScope:
    kind: str
    id: str

    def __post_init__(self) -> None:
        if not isinstance(self.kind, str) or not self.kind.strip():
            raise ValueError("MemoryScope.kind must be a non-empty string.")
        if not isinstance(self.id, str) or not self.id.strip():
            raise ValueError("MemoryScope.id must be a non-empty string.")

    def to_wire(self) -> str:
        return "{}:{}".format(self.kind, self.id)


_memory_scope: ContextVar[Optional[MemoryScope]] = ContextVar("ddtrace_llmobs_memory_scope", default=None)


def set_memory_scope(scope: MemoryScope) -> Token:
    return _memory_scope.set(_validate_scope(scope))


def get_memory_scope() -> Optional[MemoryScope]:
    return _memory_scope.get()


@contextmanager
def memory_scope(scope: MemoryScope) -> Iterator[MemoryScope]:
    token = set_memory_scope(scope)
    try:
        yield scope
    finally:
        _memory_scope.reset(token)


@dataclass(frozen=True)
class MemoryLink:
    type: str
    target_id: str
    token_count: int = 0

    def to_json(self) -> dict[str, Any]:
        return {
            "type": self.type,
            "target_id": self.target_id,
            "token_count": self.token_count,
        }


@dataclass(frozen=True)
class MemorySource:
    type: str
    agent: Optional[dict[str, str]] = None
    user: Optional[dict[str, str]] = None
    reflection: Optional[dict[str, Any]] = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"type": self.type}
        if self.agent is not None:
            out["agent"] = self.agent
        if self.user is not None:
            out["user"] = self.user
        if self.reflection is not None:
            out["reflection"] = self.reflection
        return out


@dataclass(frozen=True)
class MemoryRecord:
    id: str
    content: str
    kind: str = MEMORY_KIND
    source: Optional[Union[MemorySource, dict[str, Any]]] = None
    links: tuple[MemoryLink, ...] = ()
    created_at: Optional[str] = None
    updated_at: Optional[str] = None
    state: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "content": self.content,
        }
        if self.source is not None:
            out["source"] = self.source.to_json() if isinstance(self.source, MemorySource) else dict(self.source)
        if self.links:
            out["links"] = [link.to_json() for link in self.links]
        if self.created_at is not None:
            out["created_at"] = self.created_at
        if self.updated_at is not None:
            out["updated_at"] = self.updated_at
        if self.state is not None:
            out["state"] = self.state
        if self.metadata is not None:
            out["metadata"] = self.metadata
        return out


class MemoryBackend(Protocol):
    def search(self, scope: MemoryScope, query: str, limit: int) -> list[MemoryRecord]:
        ...

    def get(self, scope: MemoryScope, id: str) -> Optional[MemoryRecord]:
        ...

    def persist(self, scope: MemoryScope, content: str, source: MemorySource) -> MemoryRecord:
        ...


class EmptyMemoryBackend:
    def search(self, scope: MemoryScope, query: str, limit: int) -> list[MemoryRecord]:
        return []

    def get(self, scope: MemoryScope, id: str) -> Optional[MemoryRecord]:
        return None

    def persist(self, scope: MemoryScope, content: str, source: MemorySource) -> MemoryRecord:
        raise MemoryPersistUnsupportedError("EmptyMemoryBackend does not support durable memory persistence.")


class DatadogMemoryBackend:
    def __init__(self, client: Any, ml_app: Optional[str] = None) -> None:
        self._client = client
        self._ml_app = resolve_ml_app(ml_app)

    def search(self, scope: MemoryScope, query: str, limit: int) -> list[MemoryRecord]:
        data = self._post(
            MEMORY_SEARCH_ENDPOINT,
            {
                "ml_app": self._ml_app,
                "scope": scope.to_wire(),
                "query": query,
                "limit": limit,
            },
            expected_statuses=(200,),
        )
        if not isinstance(data, list):
            raise MemoryBackendError("Memory search response data must be a list.")
        return [_memory_record_from_backend(item) for item in data]

    def get(self, scope: MemoryScope, id: str) -> Optional[MemoryRecord]:
        response = self._request(
            MEMORY_GET_ENDPOINT,
            {
                "ml_app": self._ml_app,
                "scope": scope.to_wire(),
                "id": id,
            },
        )
        if response.status == 404:
            return None
        data = self._data_from_response(response, MEMORY_GET_ENDPOINT, expected_statuses=(200,))
        return _memory_record_from_backend(data)

    def persist(self, scope: MemoryScope, content: str, source: MemorySource) -> MemoryRecord:
        data = self._post(
            MEMORY_PERSIST_ENDPOINT,
            {
                "ml_app": self._ml_app,
                "scope": scope.to_wire(),
                "source": source.to_json(),
                "content": content,
            },
            expected_statuses=(200, 201),
        )
        return _memory_record_from_backend(data)

    def _post(self, path: str, body: dict[str, Any], expected_statuses: tuple[int, ...]) -> Any:
        response = self._request(path, body)
        return self._data_from_response(response, path, expected_statuses)

    def _request(self, path: str, body: dict[str, Any]) -> Response:
        self._ensure_configured()
        try:
            return self._client.request("POST", path, body)
        except MemoryBackendError:
            raise
        except Exception as exc:
            raise MemoryBackendError("Memory backend request failed for {}: {}".format(path, exc)) from exc

    def _ensure_configured(self) -> None:
        api_key = getattr(self._client, "_api_key", None)
        app_key = getattr(self._client, "_app_key", None)
        if api_key == "":
            raise MemoryNotConfiguredError(
                "DD_API_KEY is required when using the Datadog LLMObs memory backend."
            )
        if app_key == "":
            raise MemoryNotConfiguredError(
                "DD_APP_KEY is required when using the Datadog LLMObs memory backend."
            )

    def _data_from_response(self, response: Response, path: str, expected_statuses: tuple[int, ...]) -> Any:
        if response.status not in expected_statuses:
            body = _response_body_text(response)
            if path == MEMORY_PERSIST_ENDPOINT and "not implemented" in body.lower():
                raise MemoryPersistUnsupportedError(
                    "The configured Datadog memory backend does not support persistence yet."
                )
            raise MemoryBackendError(
                "Memory backend request to {} failed with status {}: {}".format(path, response.status, body)
            )
        payload = response.get_json() or {}
        if not isinstance(payload, dict) or "data" not in payload:
            raise MemoryBackendError("Memory backend response from {} did not contain a data envelope.".format(path))
        return payload["data"]


class Memory:
    def __init__(
        self,
        scope: Optional[MemoryScope] = None,
        backend: Optional[MemoryBackend] = None,
        backend_name: str = BACKEND_INTERNAL,
        default_limit: int = 10,
    ) -> None:
        self.scope = _resolve_scope(scope)
        self.backend = backend or EmptyMemoryBackend()
        self.backend_name = backend_name
        self.default_limit = _validate_limit(default_limit, field="default_limit")

    def search(self, query: str, limit: Optional[int] = None) -> str:
        if not isinstance(query, str) or not query.strip():
            raise ValueError("memory.search: query is required")
        resolved_limit = self.default_limit if limit in (None, 0) else _validate_limit(limit, field="limit")

        with _memory_tool_span(MEMORY_TOOL_SEARCH_NAME) as span:
            _annotate_memory_tool_start(
                span=span,
                action=ACTION_SEARCH,
                op=OP_READ,
                backend=self.backend_name,
                scope=self.scope,
                input_data={"query": query, "limit": resolved_limit},
                extra={"memory.query": query, "memory.limit": resolved_limit},
            )
            records = self.backend.search(self.scope, query, resolved_limit)
            output = _tool_json([record.to_json() for record in records])
            _annotate_memory_read_result(span=span, records=records, output_data=output)
            return output

    def get(self, id: str) -> str:
        if not isinstance(id, str) or not id.strip():
            raise ValueError("memory.get: id is required")

        with _memory_tool_span(MEMORY_TOOL_GET_NAME) as span:
            _annotate_memory_tool_start(
                span=span,
                action=ACTION_GET,
                op=OP_READ,
                backend=self.backend_name,
                scope=self.scope,
                input_data={"id": id},
            )
            record = self.backend.get(self.scope, id)
            if record is None:
                output = _tool_json({"result": RESULT_NOT_FOUND, "id": id})
                _annotate_memory_read_result(
                    span=span,
                    records=[],
                    output_data=output,
                    extra={
                        "memory.id": id,
                        "memory.result": RESULT_NOT_FOUND,
                        "memory.state": RESULT_NOT_FOUND,
                    },
                )
                return output

            output = _tool_json(record.to_json())
            _annotate_memory_read_result(
                span=span,
                records=[record],
                output_data=output,
                extra={"memory.kind": record.kind},
            )
            return output

    def persist(self, content: str) -> str:
        if not isinstance(content, str) or not content.strip():
            raise ValueError("memory.persist: content is required")

        with _memory_tool_span(MEMORY_TOOL_PERSIST_NAME) as span:
            _annotate_memory_tool_start(
                span=span,
                action=ACTION_PERSIST,
                op=OP_WRITE,
                backend=self.backend_name,
                scope=self.scope,
                input_data={"content": content},
            )
            source = _agent_source_from_span(span)
            record = self.backend.persist(self.scope, content, source)
            output = _tool_json(record.to_json())
            _annotate_memory_write_result(span=span, record=record, source=source, output_data=output)
            return output


MEMORY_SEARCH_TOOL_SCHEMA = {
    "name": MEMORY_TOOL_SEARCH_NAME,
    "description": (
        "Search durable memories by semantic query. Returns matching memories with outbound links such as related "
        "memories or source transcripts. Scope is resolved automatically by the runtime; do not supply it."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search query."},
            "limit": {"type": "integer", "description": "Maximum results. Default 10."},
        },
        "required": ["query"],
        "additionalProperties": False,
    },
}

MEMORY_GET_TOOL_SCHEMA = {
    "name": MEMORY_TOOL_GET_NAME,
    "description": (
        "Fetch the full content of a memory or artifact by id. Use to drill into targets returned by memory_search. "
        "Returns the node content and its outbound links for further traversal."
    ),
    "parameters": {
        "type": "object",
        "properties": {"id": {"type": "string", "description": "Memory or artifact id from memory_search."}},
        "required": ["id"],
        "additionalProperties": False,
    },
}

MEMORY_PERSIST_TOOL_SCHEMA = {
    "name": MEMORY_TOOL_PERSIST_NAME,
    "description": (
        "Save a durable memory, such as a stable preference, recurring goal, or constraint. Not for transient "
        "one-off requests. Scope and session are resolved automatically."
    ),
    "parameters": {
        "type": "object",
        "properties": {"content": {"type": "string", "description": "Durable memory content to save."}},
        "required": ["content"],
        "additionalProperties": False,
    },
}


def _resolve_scope(scope: Optional[MemoryScope]) -> MemoryScope:
    resolved = scope if scope is not None else get_memory_scope()
    if resolved is None:
        raise ValueError("Memory requires a scope. Pass scope=... or use memory_scope(...).")
    return _validate_scope(resolved)


def _validate_scope(scope: MemoryScope) -> MemoryScope:
    if not isinstance(scope, MemoryScope):
        raise TypeError("memory scope must be a MemoryScope.")
    return scope


def _validate_limit(limit: Optional[int], field: str) -> int:
    try:
        resolved = int(limit)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        raise ValueError("memory.{} must be a positive integer.".format(field)) from None
    if resolved <= 0:
        raise ValueError("memory.{} must be a positive integer.".format(field))
    return resolved


@contextmanager
def _memory_tool_span(name: str) -> Iterator[Any]:
    from ddtrace.llmobs import LLMObs

    # AIDEV-NOTE: Direct Memory(..., backend=...) construction is allowed for tests and offline tools before
    # LLMObs.enable(); tracing is a no-op in that mode, but backend operations still run.
    if not LLMObs.enabled:
        yield None
        return

    with LLMObs.tool(name=name) as span:
        yield span


def _annotate_memory_tool_start(
    span: Any,
    action: str,
    op: str,
    backend: str,
    scope: MemoryScope,
    input_data: dict[str, Any],
    extra: Optional[dict[str, Any]] = None,
) -> None:
    if span is None:
        return
    metadata = {
        "kind": MEMORY_KIND,
        "memory.op": op,
        "memory.action": action,
        "memory.backend": backend,
        "memory.scope": scope.kind,
        "memory.scope_id": scope.id,
    }
    if extra:
        metadata.update(extra)
    tags = {
        "tool.kind": MEMORY_KIND,
        "memory.op": op,
        "memory.action": action,
        "memory.backend": backend,
        "memory.scope": scope.kind,
    }
    _annotate(span=span, input_data=input_data, metadata=metadata, tags=tags)


def _annotate_memory_read_result(
    span: Any,
    records: list[MemoryRecord],
    output_data: str,
    extra: Optional[dict[str, Any]] = None,
) -> None:
    if span is None:
        return
    ids = [record.id for record in records]
    metadata: dict[str, Any] = {
        "memory.count": len(ids),
        "memory.ids": ids,
    }
    if len(ids) == 1:
        metadata["memory.id"] = ids[0]
    if extra:
        metadata.update(extra)
    metrics = _total_tokens_metric(record.content for record in records)
    _annotate(span=span, output_data=output_data, metadata=metadata, metrics=metrics)


def _annotate_memory_write_result(span: Any, record: MemoryRecord, source: MemorySource, output_data: str) -> None:
    if span is None:
        return
    metadata: dict[str, Any] = {
        "memory.id": record.id,
        "memory.source": _source_type(record.source) or source.type,
    }
    state = record.state
    if state is not None:
        metadata["memory.state"] = state
    agent = source.agent or {}
    for key in ("session_id", "trace_id", "span_id"):
        if agent.get(key):
            metadata["memory.source.{}".format(key)] = agent[key]
    metrics = _total_tokens_metric([record.content])
    _annotate(span=span, output_data=output_data, metadata=metadata, metrics=metrics)


def _annotate(
    span: Any,
    input_data: Optional[Any] = None,
    output_data: Optional[Any] = None,
    metadata: Optional[dict[str, Any]] = None,
    metrics: Optional[dict[str, float]] = None,
    tags: Optional[dict[str, str]] = None,
) -> None:
    from ddtrace.llmobs import LLMObs

    kwargs = {
        "span": span,
        "input_data": input_data,
        "output_data": output_data,
        "metadata": metadata,
        "tags": tags,
    }
    if metrics:
        kwargs["metrics"] = metrics
    LLMObs.annotate(**kwargs)


def _agent_source_from_span(span: Any) -> MemorySource:
    agent: dict[str, str] = {}
    if span is not None:
        session_id = get_llmobs_session_id(span)
        trace_id = get_llmobs_trace_id(span) or format_trace_id(span.trace_id)
        span_id = str(span.span_id)
        if session_id:
            agent["session_id"] = session_id
        if trace_id:
            agent["trace_id"] = trace_id
        if span_id:
            agent["span_id"] = span_id
    return MemorySource(type="agent", agent=agent)


def _total_tokens_metric(contents: Any) -> Optional[dict[str, float]]:
    total = sum(_estimated_tokens(content) for content in contents)
    if total <= 0:
        return None
    return {"total_tokens": float(total)}


def _estimated_tokens(content: str) -> int:
    return len(content) // 4


def _tool_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _memory_record_from_backend(payload: Any) -> MemoryRecord:
    if not isinstance(payload, dict):
        raise MemoryBackendError("Memory backend record must be a JSON object.")
    links = payload.get("links") or payload.get("memory_links") or ()
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else None
    return MemoryRecord(
        id=str(payload.get("id") or ""),
        content=str(payload.get("content") or ""),
        kind=str(payload.get("kind") or MEMORY_KIND),
        source=_memory_source_from_backend(payload.get("source")),
        links=tuple(_memory_link_from_backend(link) for link in links),
        created_at=_string_or_none(payload.get("created_at")),
        updated_at=_string_or_none(payload.get("updated_at")),
        state=_string_or_none(payload.get("state")),
        metadata=metadata,
    )


def _memory_link_from_backend(payload: Any) -> MemoryLink:
    if not isinstance(payload, dict):
        raise MemoryBackendError("Memory backend link must be a JSON object.")
    return MemoryLink(
        type=str(payload.get("type") or ""),
        target_id=str(payload.get("target_id") or ""),
        token_count=int(payload.get("token_count") or 0),
    )


def _memory_source_from_backend(payload: Any) -> Optional[Union[MemorySource, dict[str, Any]]]:
    if payload is None:
        return None
    if not isinstance(payload, dict):
        raise MemoryBackendError("Memory backend source must be a JSON object.")
    source_type = payload.get("type")
    if not source_type:
        return dict(payload)
    return MemorySource(
        type=str(source_type),
        agent=_string_dict_or_none(payload.get("agent")),
        user=_string_dict_or_none(payload.get("user")),
        reflection=payload.get("reflection") if isinstance(payload.get("reflection"), dict) else None,
    )


def _source_type(source: Optional[Union[MemorySource, dict[str, Any]]]) -> Optional[str]:
    if source is None:
        return None
    if isinstance(source, MemorySource):
        return source.type
    source_type = source.get("type")
    return str(source_type) if source_type else None


def _string_dict_or_none(value: Any) -> Optional[dict[str, str]]:
    if not isinstance(value, dict):
        return None
    return {str(k): str(v) for k, v in value.items()}


def _string_or_none(value: Any) -> Optional[str]:
    return None if value is None else str(value)


def _response_body_text(response: Response) -> str:
    body = response.body
    if isinstance(body, bytes):
        return body.decode("utf-8", errors="replace")
    return str(body)


__all__ = [
    "BACKEND_INTERNAL",
    "MEMORY_GET_TOOL_SCHEMA",
    "MEMORY_PERSIST_TOOL_SCHEMA",
    "MEMORY_SEARCH_TOOL_SCHEMA",
    "MEMORY_TOOL_GET_NAME",
    "MEMORY_TOOL_PERSIST_NAME",
    "MEMORY_TOOL_SEARCH_NAME",
    "DatadogMemoryBackend",
    "EmptyMemoryBackend",
    "Memory",
    "MemoryBackend",
    "MemoryBackendError",
    "MemoryLink",
    "MemoryNotConfiguredError",
    "MemoryPersistUnsupportedError",
    "MemoryRecord",
    "MemoryScope",
    "MemorySource",
    "get_memory_scope",
    "memory_scope",
    "set_memory_scope",
]
