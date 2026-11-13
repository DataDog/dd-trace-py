from unittest import mock

import pytest

from ddtrace.appsec._trace_utils import _asm_manual_keep
from ddtrace.constants import AUTO_KEEP
from ddtrace.constants import AUTO_REJECT
from ddtrace.constants import USER_KEEP
from ddtrace.internal.settings.appsec_telemetry import config as appsec_telemetry_config
from ddtrace.propagation.http import HTTPPropagator


@pytest.mark.parametrize("sca_enabled", ["true", "false"])
@pytest.mark.parametrize("appsec_enabled", [True, False])
@pytest.mark.parametrize("iast_enabled", [True, False])
def test_asm_standalone_minimum_trace_per_minute_has_no_downstream_propagation(
    tracer,  # noqa: F811
    sca_enabled,
    appsec_enabled,
    iast_enabled,  # noqa: F811
):
    if not appsec_enabled and not iast_enabled and sca_enabled == "false":
        pytest.skip("SCA, AppSec or IAST must be enabled")

    with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", sca_enabled == "true"):
        tracer.configure(appsec_enabled=appsec_enabled, apm_tracing_disabled=True, iast_enabled=iast_enabled)
        try:
            headers = {
                "x-datadog-trace-id": "1234",
                "x-datadog-parent-id": "5678",
                "x-datadog-sampling-priority": str(USER_KEEP),
                "x-datadog-origin": "synthetics",
                "x-datadog-tags": "_dd.p.test=value,any=tag",
                "ot-baggage-key1": "value1",
            }

            context = HTTPPropagator.extract(headers)

            tracer.context_provider.activate(context)

            with tracer.trace("local_root_span0") as span:
                # First span should be kept, as we keep 1 per min
                assert span.trace_id == 1234
                assert span.parent_id == 5678
                # Priority is unset
                assert span.context.sampling_priority is None
                assert not span._has_attribute("_sampling_priority_v1")
                assert span.context.dd_origin == "synthetics"
                assert "_dd.p.test" in span.context._meta
                assert "_dd.p.ts" not in span.context._meta

            next_headers = {}
            HTTPPropagator.inject(span.context, next_headers)

            # Ensure propagation of headers is interrupted
            assert "x-datadog-origin" not in next_headers
            assert "x-datadog-tags" not in next_headers
            assert "x-datadog-trace-id" not in next_headers
            assert "x-datadog-parent-id" not in next_headers
            assert "x-datadog-sampling-priority" not in next_headers

            # Span priority was unset, but as we keep 1 per min, it should be kept
            # Since we have a rate limiter, priorities used are AUTO_KEEP and AUTO_REJECT
            assert span._get_numeric_attribute("_sampling_priority_v1") == AUTO_KEEP

        finally:
            with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", False):
                tracer.configure(appsec_enabled=False, apm_tracing_disabled=False, iast_enabled=False)


@pytest.mark.parametrize("sca_enabled", ["true", "false"])
@pytest.mark.parametrize("appsec_enabled", [True, False])
@pytest.mark.parametrize("iast_enabled", [True, False])
def test_asm_standalone_missing_propagation_tags_no_appsec_event_trace_dropped(
    tracer,  # noqa: F811
    sca_enabled,
    appsec_enabled,
    iast_enabled,  # noqa: F811
):
    if not appsec_enabled and not iast_enabled and sca_enabled == "false":
        pytest.skip("SCA, AppSec or IAST must be enabled")

    with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", sca_enabled == "true"):
        tracer.configure(appsec_enabled=appsec_enabled, apm_tracing_disabled=True, iast_enabled=iast_enabled)
        try:
            with tracer.trace("local_root_span0"):
                # First span should be kept, as we keep 1 per min
                pass

            headers = {}

            context = HTTPPropagator.extract(headers)

            tracer.context_provider.activate(context)

            with tracer.trace("local_root_span") as span:
                assert "_dd.p.ts" not in span.context._meta

            next_headers = {}
            HTTPPropagator.inject(span.context, next_headers)

            # Ensure propagation of headers takes place as expected
            assert "x-datadog-origin" not in next_headers
            assert "x-datadog-tags" not in next_headers
            assert "x-datadog-trace-id" not in next_headers
            assert "x-datadog-parent-id" not in next_headers
            assert "x-datadog-sampling-priority" not in next_headers

            # Ensure span is dropped (no appsec event upstream or in this span)
            assert span._get_numeric_attribute("_sampling_priority_v1") == AUTO_REJECT
        finally:
            with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", False):
                tracer.configure(appsec_enabled=False, apm_tracing_disabled=False, iast_enabled=False)


def test_asm_standalone_missing_propagation_tags_appsec_event_present_trace_kept(tracer):  # noqa: F811
    tracer.configure(appsec_enabled=True, apm_tracing_disabled=True)
    try:
        with tracer.trace("local_root_span0"):
            # First span should be kept, as we keep 1 per min
            pass

        headers = {}

        context = HTTPPropagator.extract(headers)

        tracer.context_provider.activate(context)

        with tracer.trace("local_root_span") as span:
            _asm_manual_keep(span)
            assert "_dd.p.ts" in span.context._meta

        next_headers = {}
        HTTPPropagator.inject(span.context, next_headers)

        # Ensure propagation of headers takes place as expected
        assert "x-datadog-origin" not in next_headers
        assert "_dd.p.test=value" not in next_headers["x-datadog-tags"]
        assert "_dd.p.ts=02" in next_headers["x-datadog-tags"]
        assert next_headers["x-datadog-trace-id"] != "1234"
        assert next_headers["x-datadog-parent-id"] != "5678"
        assert next_headers["x-datadog-sampling-priority"] == str(USER_KEEP)

        # Ensure span is user keep
        assert span._get_numeric_attribute("_sampling_priority_v1") == USER_KEEP
    finally:
        tracer.configure(appsec_enabled=False, apm_tracing_disabled=False, iast_enabled=False)


@pytest.mark.parametrize("sca_enabled", ["true", "false"])
@pytest.mark.parametrize("appsec_enabled", [True, False])
@pytest.mark.parametrize("iast_enabled", [True, False])
def test_asm_standalone_missing_appsec_tag_no_appsec_event_propagation_resets(
    tracer,  # noqa: F811
    sca_enabled,
    appsec_enabled,
    iast_enabled,  # noqa: F811
):
    if not appsec_enabled and not iast_enabled and sca_enabled == "false":
        pytest.skip("SCA, AppSec or IAST must be enabled")

    with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", sca_enabled == "true"):
        tracer.configure(appsec_enabled=appsec_enabled, apm_tracing_disabled=True, iast_enabled=iast_enabled)
        try:
            with tracer.trace("local_root_span0"):
                # First span should be kept, as we keep 1 per min
                pass

            headers = {
                "x-datadog-trace-id": "1234",
                "x-datadog-parent-id": "5678",
                "x-datadog-sampling-priority": str(USER_KEEP),
                "x-datadog-origin": "synthetics",
                "x-datadog-tags": "_dd.p.test=value,any=tag",
                "ot-baggage-key1": "value1",
            }

            context = HTTPPropagator.extract(headers)

            tracer.context_provider.activate(context)

            with tracer.trace("local_root_span") as span:
                assert span.trace_id == 1234
                assert span.parent_id == 5678
                # Priority is unset
                assert span.context.sampling_priority is None
                assert not span._has_attribute("_sampling_priority_v1")
                assert span.context.dd_origin == "synthetics"
                assert "_dd.p.test" in span.context._meta
                assert "_dd.p.ts" not in span.context._meta

            next_headers = {}
            HTTPPropagator.inject(span.context, next_headers)

            # Ensure propagation of headers takes place as expected
            assert "x-datadog-origin" not in next_headers
            assert "x-datadog-tags" not in next_headers
            assert "x-datadog-trace-id" not in next_headers
            assert "x-datadog-parent-id" not in next_headers
            assert "x-datadog-sampling-priority" not in next_headers

            # Priority was unset, and trace is not kept, so it should be dropped
            # As we have a rate limiter, priorities used are AUTO_KEEP and AUTO_REJECT
            assert span._get_numeric_attribute("_sampling_priority_v1") == AUTO_REJECT
        finally:
            with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", False):
                tracer.configure(appsec_enabled=False, apm_tracing_disabled=False, iast_enabled=False)


def test_asm_standalone_missing_appsec_tag_appsec_event_present_trace_kept(
    tracer,  # noqa: F811
):
    tracer.configure(appsec_enabled=True, apm_tracing_disabled=True)
    try:
        with tracer.trace("local_root_span0"):
            # First span should be kept, as we keep 1 per min
            pass

        headers = {
            "x-datadog-trace-id": "1234",
            "x-datadog-parent-id": "5678",
            "x-datadog-sampling-priority": str(AUTO_REJECT),
            "x-datadog-origin": "synthetics",
            "x-datadog-tags": "_dd.p.test=value,any=tag",
            "ot-baggage-key1": "value1",
        }

        context = HTTPPropagator.extract(headers)

        tracer.context_provider.activate(context)

        with tracer.trace("local_root_span") as span:
            _asm_manual_keep(span)
            assert span.trace_id == 1234
            assert span.parent_id == 5678
            assert span.context.sampling_priority == USER_KEEP
            assert span.context.dd_origin == "synthetics"
            assert "_dd.p.ts" in span.context._meta
            assert span.context._meta["_dd.p.ts"] == "02"
            assert "_dd.p.test" in span.context._meta

        next_headers = {}
        HTTPPropagator.inject(span.context, next_headers)

        # Ensure propagation of headers is not reset and adds appsec tag
        assert next_headers["x-datadog-sampling-priority"] == str(USER_KEEP)
        assert next_headers["x-datadog-trace-id"] == "1234"
        assert "_dd.p.test=value" in next_headers["x-datadog-tags"]
        assert "_dd.p.ts=02" in next_headers["x-datadog-tags"]

        # Ensure span has force-keep priority now
        assert span._get_numeric_attribute("_sampling_priority_v1") == USER_KEEP

    finally:
        tracer.configure(appsec_enabled=False, apm_tracing_disabled=False, iast_enabled=False)


@pytest.mark.parametrize("upstream_priority", ["1", "2"])
@pytest.mark.parametrize("sca_enabled", ["true", "false"])
@pytest.mark.parametrize("appsec_enabled", [True, False])
@pytest.mark.parametrize("iast_enabled", [True, False])
def test_asm_standalone_present_appsec_tag_no_appsec_event_propagation_set_to_user_keep(
    tracer,  # noqa: F811
    upstream_priority,
    sca_enabled,
    appsec_enabled,
    iast_enabled,  # noqa: F811
):
    if not appsec_enabled and not iast_enabled and sca_enabled == "false":
        pytest.skip("SCA, AppSec or IAST must be enabled")

    with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", sca_enabled == "true"):
        tracer.configure(appsec_enabled=appsec_enabled, apm_tracing_disabled=True, iast_enabled=iast_enabled)
        try:
            with tracer.trace("local_root_span0"):
                # First span should be kept, as we keep 1 per min
                pass

            headers = {
                "x-datadog-trace-id": "1234",
                "x-datadog-parent-id": "5678",
                "x-datadog-sampling-priority": upstream_priority,
                "x-datadog-origin": "synthetics",
                "x-datadog-tags": "_dd.p.ts=02,any=tag",
                "ot-baggage-key1": "value1",
            }

            context = HTTPPropagator.extract(headers)

            tracer.context_provider.activate(context)

            with tracer.trace("local_root_span") as span:
                assert span.trace_id == 1234
                assert span.parent_id == 5678
                # Enforced user keep regardless of upstream priority
                assert span.context.sampling_priority == USER_KEEP
                assert span.context.dd_origin == "synthetics"
                assert span.context._meta == {
                    "_dd.origin": "synthetics",
                    "_dd.p.ts": "02",
                }
                with tracer.trace("child_span") as child_span:
                    assert child_span.trace_id == 1234
                    assert child_span.parent_id != 5678
                    assert child_span.context.sampling_priority == USER_KEEP
                    assert child_span.context.dd_origin == "synthetics"
                    assert child_span.context._meta == {
                        "_dd.origin": "synthetics",
                        "_dd.p.ts": "02",
                    }

                next_headers = {}
                HTTPPropagator.inject(span.context, next_headers)
                assert next_headers["x-datadog-origin"] == "synthetics"
                assert next_headers["x-datadog-sampling-priority"] == str(USER_KEEP)
                assert next_headers["x-datadog-trace-id"] == "1234"
                # With head sampling decision, only _dd.p.ts should be present (no _dd.p.dm)
                assert next_headers["x-datadog-tags"] == "_dd.p.ts=02"

            # Ensure span sets user keep regardless of received priority (appsec event upstream)
            assert span._get_numeric_attribute("_sampling_priority_v1") == USER_KEEP

        finally:
            with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", sca_enabled == "true"):
                tracer.configure(appsec_enabled=False, apm_tracing_disabled=False, iast_enabled=False)


@pytest.mark.parametrize("upstream_priority", ["1", "2"])
@pytest.mark.parametrize("sca_enabled", ["true", "false"])
@pytest.mark.parametrize("appsec_enabled", [True, False])
@pytest.mark.parametrize("iast_enabled", [True, False])
def test_asm_standalone_present_appsec_tag_appsec_event_present_propagation_force_keep(
    tracer,  # noqa: F811
    upstream_priority,
    sca_enabled,
    appsec_enabled,
    iast_enabled,  # noqa: F811
):
    if not appsec_enabled and not iast_enabled and sca_enabled == "false":
        pytest.skip("SCA, AppSec or IAST must be enabled")

    with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", sca_enabled == "true"):
        tracer.configure(appsec_enabled=appsec_enabled, apm_tracing_disabled=True, iast_enabled=iast_enabled)
        try:
            with tracer.trace("local_root_span0"):
                # First span should be kept, as we keep 1 per min
                pass

            headers = {
                "x-datadog-trace-id": "1234",
                "x-datadog-parent-id": "5678",
                "x-datadog-sampling-priority": upstream_priority,
                "x-datadog-origin": "synthetics",
                "x-datadog-tags": "_dd.p.ts=02,any=tag",
                "ot-baggage-key1": "value1",
            }

            context = HTTPPropagator.extract(headers)

            tracer.context_provider.activate(context)

            with tracer.trace("local_root_span") as span:
                _asm_manual_keep(span)
                assert span.trace_id == 1234
                assert span.parent_id == 5678
                assert span.context.sampling_priority == USER_KEEP  # user keep always
                assert span.context.dd_origin == "synthetics"
                assert span.context._meta == {
                    "_dd.origin": "synthetics",
                    "_dd.p.dm": "-5",
                    "_dd.p.ts": "02",
                }
                with tracer.trace("child_span") as child_span:
                    assert child_span.trace_id == 1234
                    assert child_span.parent_id != 5678
                    assert child_span.context.sampling_priority == USER_KEEP  # user keep always
                    assert child_span.context.dd_origin == "synthetics"
                    assert child_span.context._meta == {
                        "_dd.origin": "synthetics",
                        "_dd.p.dm": "-5",
                        "_dd.p.ts": "02",
                    }

                next_headers = {}
                HTTPPropagator.inject(span.context, next_headers)
                assert next_headers["x-datadog-origin"] == "synthetics"
                assert next_headers["x-datadog-sampling-priority"] == str(USER_KEEP)  # user keep always
                assert next_headers["x-datadog-trace-id"] == "1234"
                assert next_headers["x-datadog-tags"].startswith("_dd.p.ts=02,")

            # Ensure span set to user keep regardless received priority (appsec event upstream)
            assert span._get_numeric_attribute("_sampling_priority_v1") == USER_KEEP  # user keep always

        finally:
            with mock.patch.object(appsec_telemetry_config, "SCA_ENABLED", sca_enabled == "true"):
                tracer.configure(appsec_enabled=False, apm_tracing_disabled=False, iast_enabled=False)
