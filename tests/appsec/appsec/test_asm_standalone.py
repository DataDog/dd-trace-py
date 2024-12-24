#!/usr/bin/env python3
import copy

import pytest

import ddtrace
from ddtrace.contrib.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from tests.utils import override_env


@pytest.fixture(
    params=[
        {"DD_APPSEC_SCA_ENABLED": "1", "iast_enabled": True, "appsec_enabled": True, "appsec_standalone_enabled": True},
        {
            "DD_APPSEC_SCA_ENABLED": "1",
            "iast_enabled": True,
            "appsec_enabled": True,
            "appsec_standalone_enabled": False,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "1",
            "iast_enabled": True,
            "appsec_enabled": False,
            "appsec_standalone_enabled": False,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "1",
            "iast_enabled": True,
            "appsec_enabled": False,
            "appsec_standalone_enabled": True,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "1",
            "iast_enabled": False,
            "appsec_enabled": True,
            "appsec_standalone_enabled": True,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "1",
            "iast_enabled": False,
            "appsec_enabled": True,
            "appsec_standalone_enabled": False,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "1",
            "iast_enabled": False,
            "appsec_enabled": False,
            "appsec_standalone_enabled": False,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "1",
            "iast_enabled": False,
            "appsec_enabled": False,
            "appsec_standalone_enabled": True,
        },
        {"DD_APPSEC_SCA_ENABLED": "1", "appsec_enabled": True},
        {"DD_APPSEC_SCA_ENABLED": "1", "appsec_enabled": False},
        {"DD_APPSEC_SCA_ENABLED": "1", "iast_enabled": True},
        {"DD_APPSEC_SCA_ENABLED": "1", "iast_enabled": False},
        {"DD_APPSEC_SCA_ENABLED": "0", "iast_enabled": True, "appsec_enabled": True, "appsec_standalone_enabled": True},
        {
            "DD_APPSEC_SCA_ENABLED": "0",
            "iast_enabled": True,
            "appsec_enabled": True,
            "appsec_standalone_enabled": False,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "0",
            "iast_enabled": True,
            "appsec_enabled": False,
            "appsec_standalone_enabled": False,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "0",
            "iast_enabled": True,
            "appsec_enabled": False,
            "appsec_standalone_enabled": True,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "0",
            "iast_enabled": False,
            "appsec_enabled": True,
            "appsec_standalone_enabled": True,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "0",
            "iast_enabled": False,
            "appsec_enabled": True,
            "appsec_standalone_enabled": False,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "0",
            "iast_enabled": False,
            "appsec_enabled": False,
            "appsec_standalone_enabled": False,
        },
        {
            "DD_APPSEC_SCA_ENABLED": "0",
            "iast_enabled": False,
            "appsec_enabled": False,
            "appsec_standalone_enabled": True,
        },
        {"DD_APPSEC_SCA_ENABLED": "0", "appsec_enabled": True},
        {"DD_APPSEC_SCA_ENABLED": "0", "appsec_enabled": False},
        {"DD_APPSEC_SCA_ENABLED": "0", "iast_enabled": True},
        {"DD_APPSEC_SCA_ENABLED": "0", "iast_enabled": False},
    ]
)
def tracer_appsec_standalone(request, tracer):
    new_env = {k: v for k, v in request.param.items() if k.startswith("DD_")}
    with override_env(new_env):
        # Reset the config so it picks up the env var value
        ddtrace.config._reset()

        # Copy the params to a new dict, including the env var
        request_param_copy = copy.deepcopy(request.param)

        # Remove the environment variables as they are unexpected args for the tracer configure
        request.param.pop("DD_APPSEC_SCA_ENABLED", None)
        tracer.configure(api_version="v0.4", **request.param)

        yield tracer, request_param_copy

    # Reset tracer configuration
    ddtrace.config._reset()
    tracer.configure(api_version="v0.4", appsec_enabled=False, appsec_standalone_enabled=False, iast_enabled=False)


def test_appsec_standalone_apm_enabled_metric(tracer_appsec_standalone):
    tracer, args = tracer_appsec_standalone

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    if args.get("appsec_standalone_enabled", None) and (
        args.get("appsec_enabled", None)
        or args.get("iast_enabled", None)
        or args.get("DD_APPSEC_SCA_ENABLED", "0") == "1"
    ):
        assert tracer._apm_opt_out is True
        assert span.get_metric("_dd.apm.enabled") == 0.0
    else:
        assert tracer._apm_opt_out is False
        assert span.get_metric("_dd.apm.enabled") is None
