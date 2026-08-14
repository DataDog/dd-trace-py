from ddtrace.appsec._constants import STACK_TRACE
from ddtrace.appsec._exploit_prevention.stack_traces import report_stack
from tests.utils import override_global_config


def test_report_stack_uses_service_entry_span(tracer):
    config = {
        "_asm_enabled": True,
        "_ep_enabled": True,
        "_ep_stack_trace_enabled": True,
    }

    with override_global_config(config):
        with tracer.trace("request", service="service") as request_span:
            with tracer.trace("child", service="service") as child_span:
                assert report_stack(stack_id="1")

    assert child_span._get_struct_tag(STACK_TRACE.TAG) is None
    assert request_span._get_struct_tag(STACK_TRACE.TAG)[STACK_TRACE.RASP][0]["id"] == "1"


def test_report_iast_stack_uses_root_span(tracer):
    config = {
        "_ep_stack_trace_enabled": True,
        "_iast_enabled": True,
        "_iast_use_root_span": True,
    }

    with override_global_config(config):
        with tracer.trace("root") as root_span:
            with tracer.trace("child", service="child") as child_span:
                assert report_stack(stack_id="1", namespace=STACK_TRACE.IAST)

    assert child_span._get_struct_tag(STACK_TRACE.TAG) is None
    assert root_span._get_struct_tag(STACK_TRACE.TAG)[STACK_TRACE.IAST][0]["id"] == "1"
