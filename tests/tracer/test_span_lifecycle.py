from dataclasses import FrozenInstanceError

import pytest

from ddtrace import config
from ddtrace.internal.span_lifecycle import SpanActivationSubscription
from ddtrace.internal.span_lifecycle import attach_span_link
from ddtrace.internal.span_lifecycle import span_identity
from ddtrace.trace import Context


def test_activation_contract_restores_parent_and_clears(tracer):
    values = []
    subscription = SpanActivationSubscription(lambda: tracer.context_provider, values.append)
    subscription.start()
    subscription.start()
    try:
        with tracer.trace("root", span_type="web") as root:
            with tracer.trace("child") as child:
                assert values[-1].span_id == child.span_id
                assert values[-1].local_root_span_id == root.span_id
                assert values[-1].correlation_root_span_id == root.span_id
                assert values[-1].correlation_root_span_type == "web"
            assert values[-1].span_id == root.span_id
        assert values[-1] is None
        snapshot = values[0]
        with pytest.raises(FrozenInstanceError):
            snapshot.span_id = 0
        count = len(values)
        subscription.close()
        subscription.close()
        with tracer.trace("not observed"):
            pass
        assert len(values) == count
    finally:
        subscription.close()


def test_activation_ignores_other_provider(tracer):
    values = []
    subscription = SpanActivationSubscription(lambda: object(), values.append)
    subscription.start()
    try:
        with tracer.trace("other provider"):
            pass
        assert values == []
    finally:
        subscription.close()


def test_context_identity_distinguishes_local_ownership_from_correlation(tracer):
    context = Context(trace_id=123, span_id=456)
    assert span_identity(context).local_root_span_id is None
    assert span_identity(context).correlation_root_span_id == 456
    attach_span_link(context, 789, "web")
    assert span_identity(context).correlation_root_span_id == 789
    child = tracer.start_span("worker", child_of=context)
    try:
        identity = span_identity(child)
        assert identity.local_root_span_id == child.span_id
        assert identity.correlation_root_span_id == 789
        assert identity.correlation_root_span_type == "web"
    finally:
        child.finish()


def test_activation_listener_failures_do_not_break_requests(tracer, monkeypatch):
    monkeypatch.setattr(config, "_raise", False)

    def fail(*args):
        raise RuntimeError("consumer failure")

    subscription = SpanActivationSubscription(lambda: tracer.context_provider, fail)
    subscription.start()
    try:
        with tracer.trace("request") as span:
            pass
        assert span.finished
    finally:
        subscription.close()


def test_activation_follows_context_provider_replacement(tracer, monkeypatch):
    from ddtrace._trace.provider import DefaultContextProvider

    values = []
    subscription = SpanActivationSubscription(lambda: tracer.context_provider, values.append)
    subscription.start()
    previous = tracer.context_provider
    try:
        monkeypatch.setattr(tracer, "context_provider", DefaultContextProvider())
        with tracer.trace("new provider") as span:
            assert values[-1].span_id == span.span_id
            count = len(values)
            previous.activate(None)
            assert len(values) == count
            tracer.context_provider.activate(span)
        assert values[-1] is None
    finally:
        subscription.close()
