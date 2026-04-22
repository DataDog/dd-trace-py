"""Tests for client IP tag collection on the service entry span when AI Guard is enabled.

Spec: https://datadoghq.atlassian.net/wiki/spaces/AIGuard/pages/6523551943
      https://datadoghq.atlassian.net/wiki/spaces/SAAL/pages/2118779066
"""

import pytest

from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import http
from ddtrace.internal.settings._config import Config
from ddtrace.internal.settings.integration import IntegrationConfig
from ddtrace.trace import Span
from tests.appsec.ai_guard.utils import override_ai_guard_config
from tests.utils import override_global_config


NETWORK_CLIENT_IP = "network.client.ip"


@pytest.fixture
def integration_config():
    cfg = Config()
    cfg.myint = IntegrationConfig(cfg, "myint")
    return cfg.myint


@pytest.fixture
def span():
    yield Span("test.http.request")


def _ai_guard_on(extra=None):
    values = {"_ai_guard_enabled": True}
    if extra:
        values.update(extra)
    return override_ai_guard_config(values)


class TestAIGuardClientIP:
    """http.client_ip and network.client.ip collection when DD_AI_GUARD_ENABLED=true."""

    def test_ip_tags_set_from_xff_when_ai_guard_enabled(self, span, integration_config):
        with _ai_guard_on():
            set_http_meta(
                span,
                integration_config,
                request_headers={"x-forwarded-for": "8.8.8.8"},
            )

        assert span.get_tag(http.CLIENT_IP) == "8.8.8.8"
        assert span.get_tag(NETWORK_CLIENT_IP) == "8.8.8.8"

    def test_ip_tags_not_set_when_everything_disabled(self, span, integration_config):
        # Baseline: AI Guard off, AppSec off, DD_TRACE_CLIENT_IP_ENABLED off.
        with override_global_config(dict(_retrieve_client_ip=False)):
            set_http_meta(
                span,
                integration_config,
                request_headers={"x-forwarded-for": "8.8.8.8"},
            )

        assert span.get_tag(http.CLIENT_IP) is None
        assert span.get_tag(NETWORK_CLIENT_IP) is None

    def test_dd_trace_client_ip_enabled_false_ignored_when_ai_guard_enabled(self, span, integration_config):
        # Spec: DD_TRACE_CLIENT_IP_ENABLED is ignored when AI Guard is enabled.
        with override_global_config(dict(_retrieve_client_ip=False)), _ai_guard_on():
            set_http_meta(
                span,
                integration_config,
                request_headers={"x-forwarded-for": "8.8.8.8"},
            )

        assert span.get_tag(http.CLIENT_IP) == "8.8.8.8"
        assert span.get_tag(NETWORK_CLIENT_IP) == "8.8.8.8"

    def test_dd_trace_client_ip_header_override_with_ai_guard(self, span, integration_config):
        # Spec: DD_TRACE_CLIENT_IP_HEADER forces the header used for IP extraction.
        with override_global_config(dict(_client_ip_header="custom-header")), _ai_guard_on():
            set_http_meta(
                span,
                integration_config,
                request_headers={
                    "x-forwarded-for": "1.1.1.1",
                    "custom-header": "8.8.8.8",
                },
            )

        assert span.get_tag(http.CLIENT_IP) == "8.8.8.8"
        assert span.get_tag(NETWORK_CLIENT_IP) == "8.8.8.8"

    def test_public_ip_picked_from_xff_chain_with_ai_guard(self, span, integration_config):
        # Chain walks from first to last; private IPs are skipped, first public wins.
        with _ai_guard_on():
            set_http_meta(
                span,
                integration_config,
                request_headers={"x-forwarded-for": "10.0.0.1, 8.8.8.8, 9.9.9.9"},
            )

        assert span.get_tag(http.CLIENT_IP) == "8.8.8.8"
        assert span.get_tag(NETWORK_CLIENT_IP) == "8.8.8.8"

    def test_peer_ip_fallback_with_ai_guard(self, span, integration_config):
        # No header-derived IP -> peer_ip is used.
        with _ai_guard_on():
            set_http_meta(
                span,
                integration_config,
                request_headers={},
                peer_ip="4.4.4.4",
            )

        assert span.get_tag(http.CLIENT_IP) == "4.4.4.4"
        assert span.get_tag(NETWORK_CLIENT_IP) == "4.4.4.4"
