from __future__ import annotations

import asyncio
from collections.abc import Sequence
import logging
import threading
from typing import Any
import uuid

import pytest
import pytest_asyncio
from temporalio.client import Client
from temporalio.testing import WorkflowEnvironment

from ddtrace.contrib.internal.temporal import DatadogTracingInterceptor
from ddtrace.internal.writer.writer import TraceWriter
from ddtrace.trace import tracer as _dd_tracer


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.get_event_loop_policy().new_event_loop()
    yield loop
    loop.close()


@pytest_asyncio.fixture(scope="session")
async def env() -> Any:
    env = await WorkflowEnvironment.start_local()
    yield env
    await env.shutdown()


@pytest_asyncio.fixture
async def client(env: WorkflowEnvironment) -> Client:
    return env.client


class _SpanCollector(TraceWriter):
    """Minimal ddtrace-compatible writer that captures emitted span batches in memory.

    Installed as the span aggregator's writer, it records spans that the aggregator
    flushes without forwarding them to a Datadog agent.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._spans: list[Any] = []

    def write(self, spans: Sequence[Any] | None = None) -> None:
        if spans:
            with self._lock:
                self._spans.extend(spans)

    def flush_queue(self) -> None:
        pass

    def stop(self, timeout: float | None = None) -> None:
        pass

    def recreate(self, appsec_enabled: bool | None = None, llmobs_enabled: bool | None = None) -> _SpanCollector:
        return self

    @property
    def spans(self) -> list[Any]:
        with self._lock:
            return list(self._spans)

    def by_op(self, op: str) -> list[Any]:
        target = f"temporal.{op}"
        return [s for s in self.spans if s.name == target]

    def one(self, op: str) -> Any:
        found = self.by_op(op)
        assert len(found) == 1, f"Expected exactly 1 '{op}' span, got {len(found)}: {[s.name for s in found]}"
        return found[0]

    def tag(self, span: Any, key: str) -> Any:
        """Get a Temporal-namespaced tag or metric from a span.

        ddtrace stores integer/float values as *metrics* rather than string
        tags, so this helper checks both storages and returns whichever is set.
        """
        if not key.startswith("temporal."):
            key = "temporal." + key
        value = span.get_tag(key)
        if value is None:
            value = span.get_metric(key)
        return value


@pytest.fixture
def span_collector() -> Any:
    """Replace the global ddtrace writer with an in-memory collector."""
    collector = _SpanCollector()
    orig = _dd_tracer._span_aggregator.writer
    _dd_tracer._span_aggregator.writer = collector
    yield collector
    _dd_tracer._span_aggregator.writer = orig


class _LogCollector:
    def __init__(self) -> None:
        self.records: list[logging.LogRecord] = []

    def by_message(self, substr: str) -> list[logging.LogRecord]:
        return [r for r in self.records if substr in r.getMessage()]


@pytest.fixture
def log_collector() -> Any:
    import ddtrace

    state = _LogCollector()

    class _Handler(logging.Handler):
        def emit(self, record: logging.LogRecord) -> None:
            state.records.append(record)

    handler = _Handler()
    loggers = [
        logging.getLogger("temporalio.workflow"),
        logging.getLogger("temporalio.activity"),
    ]
    orig_levels = [(lg, lg.level) for lg in loggers]
    for lg in loggers:
        lg.setLevel(logging.DEBUG)
        lg.addHandler(handler)
    ddtrace.patch(logging=True)
    try:
        yield state
    finally:
        for lg, level in orig_levels:
            lg.removeHandler(handler)
            lg.setLevel(level)


def _make_interceptor(**kwargs: Any) -> DatadogTracingInterceptor:
    return DatadogTracingInterceptor(service_name="test-svc", **kwargs)


def _traced_client(client: Client, interceptor: DatadogTracingInterceptor) -> Client:
    """Return a new client that carries the Datadog interceptor.

    The Temporal Worker automatically merges client interceptors into its own
    list, so registering the interceptor only on the client avoids
    double-registration in the worker interceptor chain.
    """
    cfg = client.config()
    cfg["interceptors"] = [interceptor]
    return Client(**cfg)


def _task_queue() -> str:
    return f"dd-test-{uuid.uuid4()}"
