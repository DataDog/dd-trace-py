import os

import pytest

import ddtrace
from ddtrace.internal import agent
from ddtrace.internal.ci_visibility import CIVisibility
from ddtrace.internal.ci_visibility.constants import AGENTLESS_ENDPOINT
from ddtrace.internal.ci_visibility.constants import EVP_PROXY_AGENT_ENDPOINT
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_EVENT_VALUE
from ddtrace.internal.ci_visibility.constants import EVP_SUBDOMAIN_HEADER_NAME
from ddtrace.tracer import Tracer
from tests.utils import override_env


AGENT_VERSION = os.environ.get("AGENT_VERSION")


@pytest.mark.skipif(AGENT_VERSION == "testagent", reason="Test agent doesn't support evp proxy.")
def test_civisibility_intake_with_evp_available():
    with override_env(dict(DD_API_KEY="foobar.baz", DD_SITE="foo.bar", DD_CIVISIBILITY_AGENTLESS_ENABLED="0")):
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        t = Tracer()
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
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        with pytest.raises(EnvironmentError):
            CIVisibility.enable()


def test_civisibility_intake_with_apikey():
    with override_env(dict(DD_API_KEY="foobar.baz", DD_SITE="foo.bar", DD_CIVISIBILITY_AGENTLESS_ENABLED="1")):
        ddtrace.internal.ci_visibility.recorder.ddconfig = ddtrace.settings.Config()
        t = Tracer()
        CIVisibility.enable(tracer=t)
        assert CIVisibility._instance.tracer._writer._endpoint == AGENTLESS_ENDPOINT
        assert CIVisibility._instance.tracer._writer.intake_url == "https://citestcycle-intake.foo.bar"
        CIVisibility.disable()
