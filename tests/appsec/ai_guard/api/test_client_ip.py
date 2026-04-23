"""Tests for client IP tag collection on the service-entry span when AI Guard is enabled.

Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6523551943
      https://datadoghq.atlassian.net/wiki/spaces/SAAL/pages/2118779066

The IP tags are populated on the local root (service-entry) span only when an ``ai_guard``
span is actually created during the request. ``set_http_meta`` by itself only stashes the
candidate IP; ``AIGuardClient.evaluate()`` is the step that copies it onto the root span.
"""

from contextlib import nullcontext
from unittest.mock import patch

import pytest

from ddtrace.appsec._constants import AI_GUARD
from ddtrace.appsec.ai_guard import Message
from ddtrace.appsec.ai_guard import new_ai_guard_client
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from ddtrace.ext import http
from ddtrace.internal import core
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.trace import Span
from tests.appsec.ai_guard.utils import mock_evaluate_response
from tests.appsec.ai_guard.utils import override_ai_guard_config
from tests.utils import override_global_config


NETWORK_CLIENT_IP = "network.client.ip"

MESSAGES = [Message(role="user", content="hello")]

AI_GUARD_CONFIG = dict(
    _ai_guard_enabled="True",
    _ai_guard_endpoint="https://api.example.com/ai-guard",
    _dd_api_key="test-api-key",
    _dd_app_key="test-app-key",
)


@pytest.fixture(autouse=True)
def _clear_ai_guard_core_state():
    """Prevent state leakage between tests: core context is shared outside a real request."""
    core.discard_item(AI_GUARD.CLIENT_IP_CORE_KEY)
    yield
    core.discard_item(AI_GUARD.CLIENT_IP_CORE_KEY)


@pytest.fixture
def integration_config():
    cfg = Config()
    cfg.myint = IntegrationConfig(cfg, "myint")
    return cfg.myint


@pytest.fixture
def span():
    yield Span("test.http.request", span_type=SpanTypes.WEB)


def _run_request_with_evaluate(tracer, request_headers=None, peer_ip=None, extra_global_config=None):
    """Simulate a web request: start a root span, run set_http_meta with AI Guard enabled, then evaluate."""
    with override_ai_guard_config(AI_GUARD_CONFIG):
        client = new_ai_guard_client()
        ctx = override_global_config(extra_global_config) if extra_global_config else nullcontext()
        with ctx, tracer.trace("web.request") as root_span:
            dummy = Span("http.integration", span_type=SpanTypes.WEB)
            cfg = Config()
            cfg.myint = IntegrationConfig(cfg, "myint")
            set_http_meta(
                dummy,
                cfg.myint,
                request_headers=request_headers or {},
                peer_ip=peer_ip,
            )
            client.evaluate(MESSAGES)
    return root_span


class TestSetHttpMetaAlone:
    """set_http_meta alone MUST NOT tag the span when only AI Guard is enabled."""

    def test_ip_tags_not_set_on_span_when_only_ai_guard_enabled(self, span, integration_config):
        with override_ai_guard_config(AI_GUARD_CONFIG):
            set_http_meta(
                span,
                integration_config,
                request_headers={"x-forwarded-for": "8.8.8.8"},
            )

        assert span.get_tag(http.CLIENT_IP) is None
        assert span.get_tag(NETWORK_CLIENT_IP) is None

    def test_candidate_ip_stashed_in_core_when_ai_guard_enabled(self, span, integration_config):
        with override_ai_guard_config(AI_GUARD_CONFIG):
            set_http_meta(
                span,
                integration_config,
                request_headers={"x-forwarded-for": "8.8.8.8"},
            )
            assert core.find_item(AI_GUARD.CLIENT_IP_CORE_KEY) == "8.8.8.8"

    def test_ip_tags_not_set_when_everything_disabled(self, span, integration_config):
        with override_global_config(dict(_retrieve_client_ip=False)):
            set_http_meta(
                span,
                integration_config,
                request_headers={"x-forwarded-for": "8.8.8.8"},
            )

        assert span.get_tag(http.CLIENT_IP) is None
        assert span.get_tag(NETWORK_CLIENT_IP) is None
        assert core.find_item(AI_GUARD.CLIENT_IP_CORE_KEY) is None


class TestAIGuardPopulatesRootSpan:
    """When evaluate() runs, the stashed IP must land on the service-entry span."""

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_ip_set_on_root_span_after_evaluate(self, mock_execute_request, tracer, test_spans):
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        root_span = _run_request_with_evaluate(tracer, request_headers={"x-forwarded-for": "8.8.8.8"})

        assert root_span.get_tag(AI_GUARD.EVENT_TAG) == "true"
        assert root_span.get_tag(http.CLIENT_IP) == "8.8.8.8"
        assert root_span.get_tag(NETWORK_CLIENT_IP) == "8.8.8.8"

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_ip_not_set_when_evaluate_is_never_called(self, mock_execute_request, tracer, test_spans):
        # No call to evaluate() -> root span MUST NOT get client IP tags.
        with override_ai_guard_config(AI_GUARD_CONFIG), tracer.trace("web.request") as root_span:
            dummy = Span("http.integration", span_type=SpanTypes.WEB)
            cfg = Config()
            cfg.myint = IntegrationConfig(cfg, "myint")
            set_http_meta(
                dummy,
                cfg.myint,
                request_headers={"x-forwarded-for": "8.8.8.8"},
            )

        mock_execute_request.assert_not_called()
        assert root_span.get_tag(AI_GUARD.EVENT_TAG) is None
        assert root_span.get_tag(http.CLIENT_IP) is None
        assert root_span.get_tag(NETWORK_CLIENT_IP) is None

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_dd_trace_client_ip_enabled_false_ignored_when_ai_guard_runs(
        self, mock_execute_request, tracer, test_spans
    ):
        # Spec: DD_TRACE_CLIENT_IP_ENABLED is ignored once AI Guard actually reports.
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        root_span = _run_request_with_evaluate(
            tracer,
            request_headers={"x-forwarded-for": "8.8.8.8"},
            extra_global_config=dict(_retrieve_client_ip=False),
        )

        assert root_span.get_tag(http.CLIENT_IP) == "8.8.8.8"
        assert root_span.get_tag(NETWORK_CLIENT_IP) == "8.8.8.8"

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_dd_trace_client_ip_header_override(self, mock_execute_request, tracer, test_spans):
        # Spec: DD_TRACE_CLIENT_IP_HEADER forces the header used for IP extraction.
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        root_span = _run_request_with_evaluate(
            tracer,
            request_headers={"x-forwarded-for": "1.1.1.1", "custom-header": "8.8.8.8"},
            extra_global_config=dict(_client_ip_header="custom-header"),
        )

        assert root_span.get_tag(http.CLIENT_IP) == "8.8.8.8"
        assert root_span.get_tag(NETWORK_CLIENT_IP) == "8.8.8.8"

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_public_ip_picked_from_xff_chain(self, mock_execute_request, tracer, test_spans):
        # Chain walks from first to last; private IPs are skipped, first public wins.
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        root_span = _run_request_with_evaluate(
            tracer, request_headers={"x-forwarded-for": "10.0.0.1, 8.8.8.8, 9.9.9.9"}
        )

        assert root_span.get_tag(http.CLIENT_IP) == "8.8.8.8"
        assert root_span.get_tag(NETWORK_CLIENT_IP) == "8.8.8.8"

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_peer_ip_fallback(self, mock_execute_request, tracer, test_spans):
        # No header-derived IP -> peer_ip is used.
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        root_span = _run_request_with_evaluate(tracer, request_headers={}, peer_ip="4.4.4.4")

        assert root_span.get_tag(http.CLIENT_IP) == "4.4.4.4"
        assert root_span.get_tag(NETWORK_CLIENT_IP) == "4.4.4.4"

    @patch("ddtrace.appsec.ai_guard._api_client.AIGuardClient._execute_request")
    def test_core_key_cleared_after_evaluate(self, mock_execute_request, tracer, test_spans):
        # A stashed IP MUST be discarded once evaluate() applies it, so a subsequent
        # evaluate() without a fresh request can't inherit the previous request's IP.
        mock_execute_request.return_value = mock_evaluate_response("ALLOW")
        _run_request_with_evaluate(tracer, request_headers={"x-forwarded-for": "8.8.8.8"})
        assert core.find_item(AI_GUARD.CLIENT_IP_CORE_KEY) is None


class TestOutboundClientSpansDoNotStash:
    """Only inbound (WEB) spans may stash the candidate IP; client spans must be ignored."""

    def test_outbound_http_client_span_does_not_stash(self, integration_config):
        # Simulate an aiohttp-style outbound client span: span_type=HTTP, forwarded headers
        # from a downstream hop must NOT overwrite the AI Guard core key.
        client_span = Span("http.client.request", span_type=SpanTypes.HTTP)
        with override_ai_guard_config(AI_GUARD_CONFIG):
            set_http_meta(
                client_span,
                integration_config,
                request_headers={"x-forwarded-for": "8.8.8.8"},
            )

        assert core.find_item(AI_GUARD.CLIENT_IP_CORE_KEY) is None

    def test_outbound_client_does_not_overwrite_inbound_stash(self, integration_config):
        # An inbound WEB span stashes the real client IP. A later outbound client span
        # in the same trace MUST NOT overwrite it with a downstream header value.
        web_span = Span("web.request", span_type=SpanTypes.WEB)
        client_span = Span("http.client.request", span_type=SpanTypes.HTTP)
        with override_ai_guard_config(AI_GUARD_CONFIG):
            set_http_meta(
                web_span,
                integration_config,
                request_headers={"x-forwarded-for": "8.8.8.8"},
            )
            set_http_meta(
                client_span,
                integration_config,
                request_headers={"x-forwarded-for": "1.2.3.4"},
            )

        assert core.find_item(AI_GUARD.CLIENT_IP_CORE_KEY) == "8.8.8.8"
