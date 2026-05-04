"""Tests for anomaly-detection attribute propagation onto AI Guard spans.

Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6596165672

Tags listed in ``AI_GUARD.ANOMALY_DETECTION_TAGS`` must be copied from the local root
(service-entry) span onto every ``ai_guard`` span with the ``ai_guard.`` prefix, so
that anomaly detection at intake has them even if the service-entry span arrives in
a later trace chunk.
"""

from unittest.mock import patch

import pytest

from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import new_ai_guard_client
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.internal import core
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.trace import Span
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import override_ai_guard_config


MESSAGES = [Message(role="user", content="hello")]

AI_GUARD_CONFIG = dict(
    _ai_guard_enabled="True",
    _ai_guard_endpoint="https://api.example.com/ai-guard",
    _dd_api_key="test-api-key",
    _dd_app_key="test-app-key",
)


@pytest.fixture(autouse=True)
def _clear_ai_guard_core_state():
    core.discard_item(AI_GUARD.CLIENT_IP_CORE_KEY)
    yield
    core.discard_item(AI_GUARD.CLIENT_IP_CORE_KEY)


def _find_ai_guard_span(test_spans):
    for s in test_spans.spans:
        if s.name == AI_GUARD.RESOURCE_TYPE:
            return s
    raise AssertionError("AI Guard span not found")


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_all_anomaly_detection_tags_copied_from_root_span(mock_execute_request, tracer, test_spans):
    """All ANOMALY_DETECTION_TAGS present on the root span land on the ai_guard span with the ai_guard. prefix."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with override_ai_guard_config(AI_GUARD_CONFIG):
        client = new_ai_guard_client()
        with tracer.trace("web.request") as root_span:
            root_span.set_tag("http.useragent", "test-agent/1.0")
            root_span.set_tag("http.client_ip", "8.8.8.8")
            root_span.set_tag("network.client.ip", "8.8.8.8")
            root_span.set_tag("usr.id", "u12345")
            root_span.set_tag("session.id", "s12345")
            client.evaluate(MESSAGES)

    ai_guard_span = _find_ai_guard_span(test_spans)
    assert ai_guard_span.get_tag("ai_guard.http.useragent") == "test-agent/1.0"
    assert ai_guard_span.get_tag("ai_guard.http.client_ip") == "8.8.8.8"
    assert ai_guard_span.get_tag("ai_guard.network.client.ip") == "8.8.8.8"
    assert ai_guard_span.get_tag("ai_guard.usr.id") == "u12345"
    assert ai_guard_span.get_tag("ai_guard.session.id") == "s12345"


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_only_present_tags_are_copied(mock_execute_request, tracer, test_spans):
    """Tags absent from the root span MUST NOT be set on the ai_guard span."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with override_ai_guard_config(AI_GUARD_CONFIG):
        client = new_ai_guard_client()
        with tracer.trace("web.request") as root_span:
            root_span.set_tag("usr.id", "u12345")
            client.evaluate(MESSAGES)

    ai_guard_span = _find_ai_guard_span(test_spans)
    assert ai_guard_span.get_tag("ai_guard.usr.id") == "u12345"
    for tag_name in AI_GUARD.ANOMALY_DETECTION_TAGS:
        if tag_name == "usr.id":
            continue
        assert ai_guard_span.get_tag(f"ai_guard.{tag_name}") is None


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_no_root_span_does_not_raise(mock_execute_request, ai_guard_client, test_spans):
    """evaluate() outside a trace context must not raise when there's nothing to copy from."""
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    ai_guard_client.evaluate(MESSAGES)

    ai_guard_span = _find_ai_guard_span(test_spans)
    for tag_name in AI_GUARD.ANOMALY_DETECTION_TAGS:
        assert ai_guard_span.get_tag(f"ai_guard.{tag_name}") is None


@patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
def test_client_ip_from_set_http_meta_is_copied(mock_execute_request, tracer, test_spans):
    """The client IP stashed by set_http_meta lands on the entry span and is then copied
    onto the ai_guard span with the ai_guard. prefix in a single evaluate() call.
    """
    mock_execute_request.return_value = mock_evaluate_response("ALLOW")

    with override_ai_guard_config(AI_GUARD_CONFIG):
        client = new_ai_guard_client()
        with tracer.trace("web.request") as root_span:
            cfg = Config()
            cfg.myint = IntegrationConfig(cfg, "myint")
            dummy = Span("http.integration", span_type=SpanTypes.WEB)
            set_http_meta(
                dummy,
                cfg.myint,
                request_headers={"x-forwarded-for": "8.8.8.8", "user-agent": "test-agent/1.0"},
            )
            root_span.set_tag("http.useragent", "test-agent/1.0")
            client.evaluate(MESSAGES)

    ai_guard_span = _find_ai_guard_span(test_spans)
    assert ai_guard_span.get_tag("ai_guard.http.client_ip") == "8.8.8.8"
    assert ai_guard_span.get_tag("ai_guard.network.client.ip") == "8.8.8.8"
    assert ai_guard_span.get_tag("ai_guard.http.useragent") == "test-agent/1.0"
