import pytest

from ddtrace.constants import USER_KEEP
from ddtrace.internal.native._native import SpanData
from ddtrace.internal.security._trace_utils import _asm_manual_keep
from ddtrace.propagation.http import HTTPPropagator
from tests.utils import override_global_config


@pytest.mark.parametrize("child_span", [False, True])
def test_appsec_sampling_mechanism_published_before_injection(child_span):
    headers = {}

    class ProbeSpan(SpanData):
        def _override_sampling_decision(self, decision, *args, **kwargs):
            super()._override_sampling_decision(decision, *args, **kwargs)
            # Observe the published decision before the AppSec helper can update any tags.
            HTTPPropagator.inject(self.context, headers)

    root = ProbeSpan("root")
    span = root
    if child_span:
        span = ProbeSpan("child", context=root.context)
        span._inherit_from_parent(root)

    with override_global_config(dict(_propagation_style_inject=["datadog"])):
        _asm_manual_keep(span)

    assert headers["x-datadog-sampling-priority"] == str(USER_KEEP)
    assert "_dd.p.dm=-5" in headers["x-datadog-tags"].split(",")
    assert root.context._meta["_dd.p.dm"] == "-5"
    assert root._get_str_attribute("_dd.p.dm") == "-5"
    assert root._get_str_attribute("_dd.p.ts") == "02"
    assert span._get_str_attribute("_dd.p.dm") == "-5"
