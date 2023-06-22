import json

import pytest

from ddtrace._monkey import IAST_PATCH
from ddtrace._monkey import patch_iast
from ddtrace.appsec._constants import IAST
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.constants import USER_KEEP
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from tests.utils import DummyTracer
from tests.utils import override_env
from tests.utils import override_global_config


def traced_function(tracer):
    with tracer.trace("test", span_type=SpanTypes.WEB) as span:
        import hashlib

        m = hashlib.new("md5")
        m.update(b"Nobody inspects")
        m.update(b" the spammish repetition")
        num_vulnerabilities = 10
        for i in range(0, num_vulnerabilities):
            m.digest()
    return span


def test_appsec_iast_processor():
    with override_global_config(dict(_iast_enabled=True)):
        patch_iast(**IAST_PATCH)

        tracer = DummyTracer(iast_enabled=True)

        span = traced_function(tracer)
        tracer._on_span_finish(span)

        # even though it doesn't look like it, this test creates an asm_request_context
        # (this is because of a bug where feature flags leak between tests)
        # in 1.x this doesn't affect CONTEXT_KEY's accessibility. that's because it's
        # stored on the root span, which is still accessible via `span` here.
        # on this branch, CONTEXT_KEY is not accessible. this is because it's stored on the
        # current context, which ceases to exist when the trace() context manager exits.
        # trace() isn't supposed to create a context, and CONTEXT_KEY is supposed to be set on the root context
        # however, the config-leaking bug means that trace() does create a context, which it
        # later destroys along with CONTEXT_KEY
        # solution 1: fix the config leak. undesirable because a similar fix would have to be applied on
        #   all tests
        # solution 2: make core.set_item store everything on the root context
        span_report = _context.get_item(IAST.CONTEXT_KEY, span=span)
        result = span.get_tag(IAST.JSON)

        assert len(span_report.vulnerabilities) == 1
        assert len(json.loads(result)["vulnerabilities"]) == 1


@pytest.mark.parametrize("sampling_rate", ["0.0", "0.5", "1.0"])
def test_appsec_iast_processor_ensure_span_is_manual_keep(sampling_rate):
    with override_env(dict(DD_TRACE_SAMPLE_RATE=sampling_rate)), override_global_config(dict(_iast_enabled=True)):
        patch_iast(**IAST_PATCH)

        tracer = DummyTracer(iast_enabled=True)

        span = traced_function(tracer)
        tracer._on_span_finish(span)

        result = span.get_tag(IAST.JSON)

        assert len(json.loads(result)["vulnerabilities"]) == 1
        assert span.get_metric(SAMPLING_PRIORITY_KEY) is USER_KEEP
