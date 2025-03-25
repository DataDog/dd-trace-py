import os

import mock
import pytest

from ddtrace.internal import agent
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility._api_client import TestVisibilityAPISettings
from ddtrace.internal.ci_visibility.constants import AGENTLESS_ENDPOINT
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_ENDPOINT
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_EVENT_VALUE
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.internal.ci_visibility.recorder import CIVisibilityTracer
from tests.ci_visibility.util import _get_default_civisibility_ddconfig
from tests.utils import override_env


AGENT_VERSION = os.environ.get("AGENT_VERSION")


@pytest.fixture(autouse=True, scope="module")
def _dummy_check_enabled_features():
    """By default, assume that _check_enabled_features() returns an ITR-disabled response.

    Tests that need a different response should re-patch the CIVisibility object.
    """
    with mock.patch(
        "ddtrace.internal.ci_visibility.recorder.CIVisibility._check_enabled_features",
        return_value=TestVisibilityAPISettings(False, False, False, False),
    ):
        yield


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support evp proxy.")
def test_civisibility_intake_with_evp_available():
    with override_env(
        dict(DD_API_KEY="foobar.baz", DD_SITE="foo.bar", DD_CIVISIBILITY_AGENTLESS_ENABLED="0")
    ), mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()):
        t = CIVisibilityTracer()
        CIVisibility.enable(tracer=t)
        assert CIVisibility._instance.tracer._writer._endpoint == EVP_PROXY_AGENT_ENDPOINT
        assert CIVisibility._instance.tracer._writer.intake_url == agent.get_trace_url()
        assert (
            CIVisibility._instance.tracer._writer._headers[EVP_SUBDOMAIN_HEADER_NAME]
            == EVP_SUBDOMAIN_HEADER_EVENT_VALUE
        )
        CIVisibility.disable()


def test_civisibility_intake_with_missing_apikey():
    with override_env(dict(DD_SITE="foobar.baz", DD_CIVISIBILITY_AGENTLESS_ENABLED="1")):
        with mock.patch.object(CIVisibility, "__init__", return_value=None) as mock_CIVisibility_init:
            with mock.patch.object(CIVisibility, "start") as mock_CIVisibility_start, mock.patch(
                "ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()
            ):
                CIVisibility.enable()
                assert CIVisibility.enabled is False
                assert CIVisibility._instance is None
                mock_CIVisibility_init.assert_not_called()
                mock_CIVisibility_start.assert_not_called()


def test_civisibility_intake_with_apikey():
    with override_env(
        dict(DD_API_KEY="foobar.baz", DD_SITE="foo.bar", DD_CIVISIBILITY_AGENTLESS_ENABLED="1")
    ), mock.patch("ddtrace.internal.ci_visibility.recorder.ddconfig", _get_default_civisibility_ddconfig()):
        t = CIVisibilityTracer()
        CIVisibility.enable(tracer=t)
        assert CIVisibility._instance.tracer._writer._endpoint == AGENTLESS_ENDPOINT
        assert CIVisibility._instance.tracer._writer.intake_url == "https://citestcycle-intake.foo.bar"
        CIVisibility.disable()


@pytest.mark.subprocess()
def test_civisibility_intake_payloads():
    import mock

    from ddtrace.internal.ci_visibility.constants import COVERAGE_TAG_NAME
    from ddtrace.internal.ci_visibility.recorder import CIVisibilityWriter
    from ddtrace.internal.utils.http import Response
    from ddtrace.trace import tracer as t
    from tests.utils import override_env

    with override_env(dict(DD_API_KEY="foobar.baz")):
        t._writer = CIVisibilityWriter(reuse_connections=True, coverage_enabled=True)
        t._recreate()
        t._writer._conn = mock.MagicMock()
        with mock.patch("ddtrace.internal.writer.Response.from_http_response") as from_http_response:
            from_http_response.return_value.__class__ = Response
            from_http_response.return_value.status = 200
            s = t.trace("operation", service="svc-no-cov")
            s.finish()
            span = t.trace("operation2", service="my-svc2")
            span.set_tag(
                COVERAGE_TAG_NAME,
                '{"files": [{"filename": "test_cov.py", "segments": [[5, 0, 5, 0, -1]]}, '
                + '{"filename": "test_module.py", "segments": [[2, 0, 2, 0, -1]]}]}',
            )
            span.finish()
            conn = t._writer._conn
            t.shutdown()
        assert 2 <= conn.request.call_count <= 3
        assert conn.request.call_args_list[0].args[1] == "api/v2/citestcycle"
        assert (
            b"svc-no-cov" in conn.request.call_args_list[0].args[2]
        ), "requests to the cycle endpoint should include non-coverage spans"
        assert conn.request.call_args_list[1].args[1] == "api/v2/citestcov"
        assert (
            b"svc-no-cov" not in conn.request.call_args_list[1].args[2]
        ), "requests to the coverage endpoint should not include non-coverage spans"
