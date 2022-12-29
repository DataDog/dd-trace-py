import json

import pytest

from ddtrace._monkey import IAST_PATCH
from ddtrace._monkey import patch_iast
from ddtrace.constants import IAST_CONTEXT_KEY
from ddtrace.constants import IAST_JSON
from ddtrace.constants import SAMPLING_PRIORITY_KEY
from ddtrace.constants import USER_KEEP
from ddtrace.ext import SpanTypes
from ddtrace.internal import _context
from tests.utils import DummyTracer
from tests.utils import override_env


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
    with override_env(dict(DD_IAST_ENABLED="true")):
        patch_iast(**IAST_PATCH)

        tracer = DummyTracer(iast_enabled=True)

        span = traced_function(tracer)
        tracer._on_span_finish(span)

        span_report = _context.get_item(IAST_CONTEXT_KEY, span=span)
        result = span.get_tag(IAST_JSON)

        assert len(span_report.vulnerabilities) == 1
        assert len(json.loads(result)["vulnerabilities"]) == 1


@pytest.mark.parametrize("sampling_rate", ["0.0", "0.5", "1.0"])
def test_appsec_iast_processor_ensure_span_is_manual_keep(sampling_rate):
    with override_env(dict(DD_IAST_ENABLED="true", DD_TRACE_SAMPLE_RATE=sampling_rate)):
        patch_iast(**IAST_PATCH)

        tracer = DummyTracer(iast_enabled=True)

        span = traced_function(tracer)
        tracer._on_span_finish(span)

        result = span.get_tag(IAST_JSON)

        assert len(json.loads(result)["vulnerabilities"]) == 1
        assert span.get_metric(SAMPLING_PRIORITY_KEY) is USER_KEEP
