"""Unit checks for IAST request ownership; FastAPI tests cover real request execution."""

import asyncio
from contextlib import nullcontext
from contextvars import ContextVar
from types import SimpleNamespace
from unittest import mock

import pytest

from ddtrace.appsec._iast import _iast_request_context as reporting
from ddtrace.appsec._iast import _request_lifecycle as lifecycle
from ddtrace.appsec._iast._constants import IAST
from ddtrace.appsec._iast._iast_env import IASTEnvironment
from ddtrace.contrib._events.web_request import WebRequestCompleting
from ddtrace.contrib._events.web_request import WebRequestReady
from ddtrace.internal import core
from ddtrace.trace import Span


@pytest.fixture
def ready(monkeypatch):
    monkeypatch.setattr(lifecycle.asm_config, "_iast_enabled", True)
    monkeypatch.setattr(lifecycle, "IAST_CONTEXT", ContextVar("test_iast_context", default=None))
    monkeypatch.setattr(lifecycle, "get_tainted_object_map_by_ctx_id", lambda _id: False)
    ctx = core.ExecutionContext(
        "asgi.request", integration_config=SimpleNamespace(integration_name="fastapi"), is_subapp=False
    )
    return WebRequestReady(ctx, mock.sentinel.span)


@pytest.mark.parametrize("context_id", [None, 0])
def test_ready_records_admission_once(ready, context_id):
    with mock.patch.object(
        lifecycle, "_iast_start_request", side_effect=lambda _span: lifecycle.IAST_CONTEXT.set(context_id)
    ) as start:
        lifecycle.IastRequestReadySubscriber.on_event(ready)
        lifecycle.IastRequestReadySubscriber.on_event(ready)
        nested = core.ExecutionContext(
            "asgi.request",
            parent=ready.ctx,
            integration_config=ready.ctx.get_item("integration_config"),
            is_subapp=True,
        )
        lifecycle.IastRequestReadySubscriber.on_event(WebRequestReady(nested, mock.sentinel.child))
    start.assert_called_once_with(ready.span)
    assert ready.ctx.get_item(lifecycle._REQUEST_STATE).context_id == context_id


@pytest.mark.parametrize("context_id", [None, 0])
def test_ready_borrows_existing_analysis_without_finishing_it(ready, monkeypatch, context_id):
    lifecycle.IAST_CONTEXT.set(context_id)
    ready.ctx.set_item(IAST.REQUEST_CONTEXT_KEY, IASTEnvironment(mock.sentinel.outer, context_id))
    monkeypatch.setattr(lifecycle, "get_tainted_object_map_by_ctx_id", lambda _id: True)
    with (
        mock.patch.object(lifecycle, "_iast_start_request") as start,
        mock.patch.object(lifecycle, "_iast_end_request") as end,
    ):
        lifecycle.IastRequestReadySubscriber.on_event(ready)
        lifecycle.IastRequestCompletingSubscriber.on_event(WebRequestCompleting(ready.ctx, ready.span))
    start.assert_not_called()
    end.assert_not_called()
    assert lifecycle.IAST_CONTEXT.get() == context_id


@pytest.mark.parametrize("expired", [False, True])
def test_ready_separates_a_foreign_environment(ready, monkeypatch, expired):
    parent = core.ExecutionContext("other.execution")
    environment = IASTEnvironment(mock.sentinel.outer, 1)
    parent.set_item(IAST.REQUEST_CONTEXT_KEY, environment)
    ready.ctx.parent = parent
    monkeypatch.setattr(lifecycle, "get_tainted_object_map_by_ctx_id", lambda _id: not expired)

    def admit(_span):
        assert lifecycle._get_iast_env() is None

    with mock.patch.object(lifecycle, "_iast_start_request", side_effect=admit) as start:
        lifecycle.IastRequestReadySubscriber.on_event(ready)
    start.assert_called_once_with(ready.span)
    assert parent.get_item(IAST.REQUEST_CONTEXT_KEY) is (None if expired else environment)


def test_ready_releases_partial_admission_on_failure(ready):
    def fail(_span):
        lifecycle.IAST_CONTEXT.set(0)
        raise RuntimeError("admission failed")

    with (
        mock.patch.object(lifecycle, "_iast_start_request", side_effect=fail),
        mock.patch.object(lifecycle, "_iast_finish_request") as finish,
    ):
        with pytest.raises(RuntimeError, match="admission failed"):
            lifecycle.IastRequestReadySubscriber.on_event(ready)
    finish.assert_called_once_with(ready.span)
    assert ready.ctx.get_item(lifecycle._REQUEST_STATE).completed


@pytest.mark.parametrize("owner", ["absent", "other_span", "completed"])
def test_completing_ignores_requests_it_does_not_own(ready, owner):
    if owner != "absent":
        span = mock.sentinel.other if owner == "other_span" else ready.span
        ready.ctx.set_item(lifecycle._REQUEST_STATE, lifecycle._RequestState(span, 0, completed=owner == "completed"))
    with mock.patch.object(lifecycle, "_iast_end_request") as end:
        lifecycle.IastRequestCompletingSubscriber.on_event(WebRequestCompleting(ready.ctx, ready.span))
    end.assert_not_called()


@pytest.mark.parametrize(
    "caller_id,outcome",
    [
        (0, "reported"),
        (None, "reported"),
        (1, "reported"),
        (1, "unfinished"),
        (1, RuntimeError),
        (1, asyncio.CancelledError),
    ],
)
def test_completing_reports_once_cleans_up_and_restores_caller(ready, caller_id, outcome):
    ready.ctx.set_item(lifecycle._REQUEST_STATE, lifecycle._RequestState(ready.span, 0))
    lifecycle.IAST_CONTEXT.set(caller_id)
    caller = core.current

    def report(_span):
        assert core.current is ready.ctx
        assert lifecycle.IAST_CONTEXT.get() == 0
        if isinstance(outcome, type):
            raise outcome()
        if outcome == "reported":
            lifecycle.IAST_CONTEXT.set(None)

    with (
        mock.patch.object(lifecycle, "_iast_end_request", side_effect=report) as end,
        mock.patch.object(
            lifecycle, "_iast_finish_request", side_effect=lambda _span: lifecycle.IAST_CONTEXT.set(None)
        ) as finish,
    ):
        with pytest.raises(outcome) if isinstance(outcome, type) else nullcontext():
            lifecycle.IastRequestCompletingSubscriber.on_event(WebRequestCompleting(ready.ctx, ready.span))
        lifecycle.IastRequestCompletingSubscriber.on_event(WebRequestCompleting(ready.ctx, ready.span))
    end.assert_called_once_with(ready.span)
    assert finish.call_count == (outcome != "reported")
    assert core.current is caller
    assert lifecycle.IAST_CONTEXT.get() == (None if caller_id == 0 else caller_id)
    assert ready.ctx.get_item(IAST.REQUEST_CONTEXT_KEY, mock.sentinel.missing) is None


def test_reporting_resets_the_environment_it_started_with(ready):
    environment, other_environment = mock.Mock(), mock.Mock()
    current_environment = environment

    def finish(_span):
        nonlocal current_environment
        current_environment = other_environment

    with (
        mock.patch.object(reporting, "_get_iast_env", side_effect=lambda: current_environment),
        mock.patch.object(reporting.base, "is_iast_request_enabled", return_value=True),
        mock.patch.object(reporting, "_create_and_attach_iast_report_to_span", side_effect=finish),
        mock.patch.object(reporting, "reset_request_vulnerabilities") as reset,
    ):
        reporting._iast_end_request(Span("request"))
    reset.assert_called_once_with(environment)
    environment.iast_hash_object_tracking.clear.assert_called_once_with()
    other_environment.iast_hash_object_tracking.clear.assert_not_called()
