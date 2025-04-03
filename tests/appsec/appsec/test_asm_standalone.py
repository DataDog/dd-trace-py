#!/usr/bin/env python3
import copy

import pytest

import ddtrace
from ddtrace.contrib.internal.trace_utils import set_http_meta
from ddtrace.ext import SpanTypes
from tests.utils import override_env


@pytest.fixture(
    params=[
        {"DD_APPSEC_SCA_ENABLED": sca, "iast_enabled": iast, "appsec_enabled": appsec, "apm_tracing_disabled": apm}
        for sca in ["1", "0"]
        for iast in [True, False]
        for appsec in [True, False]
        for apm in [True, False]
    ]
    + [{"DD_APPSEC_SCA_ENABLED": sca, "appsec_enabled": appsec} for sca in ["1", "0"] for appsec in [True, False]]
    + [{"DD_APPSEC_SCA_ENABLED": sca, "iast_enabled": iast} for sca in ["1", "0"] for iast in [True, False]]
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
        tracer.configure(**request.param)

        yield tracer, request_param_copy

    # Reset tracer configuration
    ddtrace.config._reset()
    tracer.configure(appsec_enabled=False, apm_tracing_disabled=False, iast_enabled=False)


def test_appsec_standalone_apm_enabled_metric(tracer_appsec_standalone):
    tracer, args = tracer_appsec_standalone

    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        set_http_meta(span, {}, raw_uri="http://example.com/.git", status_code="404")

    if args.get("apm_tracing_disabled", None) and (
        args.get("appsec_enabled", None)
        or args.get("iast_enabled", None)
        or args.get("DD_APPSEC_SCA_ENABLED", "0") == "1"
    ):
        assert span.get_metric("_dd.apm.enabled") == 0.0
    else:
        assert span.get_metric("_dd.apm.enabled") is None
