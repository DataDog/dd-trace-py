import os
import sys
import sysconfig
import time
from typing import Any
from typing import Optional
from unittest import mock

import httpretty
import pytest

from ddtrace import config
import ddtrace.internal.settings._core as settings_core
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings._telemetry import config as telemetry_config
import ddtrace.internal.telemetry
from ddtrace.internal.telemetry.constants import TELEMETRY_APM_PRODUCT
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.telemetry.data import get_application
from ddtrace.internal.telemetry.data import get_host_info
from ddtrace.internal.telemetry.writer import TelemetryWriter
from ddtrace.internal.telemetry.writer import get_runtime_id
from ddtrace.internal.utils.version import _pep440_to_semver
from tests.utils import call_program
from tests.utils import override_global_config


class _SyntheticDDConfig(DDConfig):
    """
    A minimal DDConfig used to exercise report_configuration()'s own walking logic
    (public/private/sensitive filtering, source + config_id resolution) without any
    knowledge of real product settings.
    """

    __prefix__ = "dd.test.synthetic"

    public_setting = DDConfig.v(str, "public_setting", default="pub_default")
    _private_setting = DDConfig.v(str, "private_setting", default="priv_default", private=True)
    sensitive_setting = DDConfig.v(str, "sensitive_setting", default="sens_default")
    bool_setting = DDConfig.v(bool, "bool_setting", default=False)
    float_setting = DDConfig.v(float, "float_setting", default=0.0)
    # NOTE: DDConfig.config_id is a single instance attribute overwritten during __init__'s
    # field-iteration loop, not a per-field map, so it only reflects whichever fleet-sourced
    # field was processed last. Keep this field last so the config_id assertion below is stable.
    fleet_setting = DDConfig.v(str, "fleet_setting", default="fleet_default")


def test_app_started_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that app_started() queues a valid telemetry request which is then sent by periodic()"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        # App started should be queued by the first periodic call
        telemetry_writer.periodic(force_flush=True)
        requests = test_agent_session.get_requests()
        assert len(requests) == 1
        assert requests[0]["headers"]["DD-Telemetry-Request-Type"] == "message-batch"
        app_started_events = test_agent_session.get_events("app-started")
        assert len(app_started_events) == 1
        validate_request_body(app_started_events[0], None, "app-started")
        assert len(app_started_events[0]["payload"]) == 2
        assert app_started_events[0]["payload"].get("configuration")
        assert app_started_events[0]["payload"].get("products")

        # app-started always reports the interpreter's build info, sourced from sysconfig
        # rather than from any product configuration, so it's telemetry's own data, not a
        # dependency on another component's settings.
        configs_by_name = {c["name"]: c for c in app_started_events[0]["payload"]["configuration"]}
        for name, sysconfig_key in (
            ("python_soabi", "SOABI"),
            ("python_host_gnu_type", "HOST_GNU_TYPE"),
            ("python_build_gnu_type", "BUILD_GNU_TYPE"),
        ):
            assert configs_by_name[name]["origin"] == "unknown"
            assert configs_by_name[name]["value"] == sysconfig.get_config_var(sysconfig_key)


def test_update_dependencies_event(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    # Import httppretty after ddtrace is imported, this ensures that the module is sent in a dependencies event
    # Imports httpretty twice and ensures only one dependency entry is sent
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess("import xmltodict", env=env)
    assert status == 0, stderr
    deps = test_agent_session.get_dependencies("xmltodict")
    assert len(deps) == 1, deps


def test_endpoint_discovery_event(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    # Import httppretty after ddtrace is imported, this ensures that the module is sent in a dependencies event
    # Imports httpretty twice and ensures only one dependency entry is sent

    mini_django_app = """
from os import path as osp
def rel_path(*p): return osp.normpath(osp.join(rel_path.path, *p))
rel_path.path = osp.abspath(osp.dirname(__file__))
this = osp.splitext(osp.basename(__file__))[0]
from django.conf import settings
SETTINGS = dict(
    DATABASES = {},
    DEBUG=True,
    TEMPLATE_DEBUG=True,
    ROOT_URLCONF = this
)
SETTINGS['DATABASES']={
    'default':{
        'ENGINE':'django.db.backends.sqlite3',
        'NAME':rel_path('db')
    }
}

if __name__=='__main__':
    settings.configure(**SETTINGS)

if __name__ == '__main__':
    from django.core import management
    management.execute_from_command_line()

from django.urls import path
from django.http import HttpResponse
from django.views.decorators.http import require_http_methods
@require_http_methods(["GET"])
def view_name(request):
    return HttpResponse('response text')
def mini_app(request):
    return HttpResponse('response text')
urlpatterns = [ path('mini_app/',mini_app), path('view_name/', view_name) ]
"""

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(mini_django_app, env=env)
    assert status == 0, stderr
    deps = test_agent_session.get_dependencies("django")
    assert len(deps) == 1, deps

    events = test_agent_session.get_events("app-endpoints")
    assert len(events) == 1, events
    payload = events[0]["payload"]
    assert payload["is_first"] is True
    endpoints = payload["endpoints"]
    assert len(endpoints) == 2, endpoints
    assert any(
        e["path"] == "mini_app/" and e["method"] == "*" and e["operation_name"] == "django.request" for e in endpoints
    ), endpoints
    assert any(
        e["path"] == "view_name/"
        and e["method"] == "GET"
        and e["resource_name"] == "GET view_name/"
        and e["operation_name"] == "django.request"
        for e in endpoints
    ), endpoints


def test_instrumentation_source_config(
    test_agent_session, ddtrace_run_python_code_in_subprocess, run_python_code_in_subprocess
):
    env = os.environ.copy()
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = call_program("ddtrace-run", sys.executable, "-c", "", env=env)
    assert status == 0, stderr
    configs = test_agent_session.get_configurations("instrumentation_source")
    assert configs and configs[-1]["value"] == "cmd_line"
    test_agent_session.clear()

    _, stderr, status, _ = call_program(sys.executable, "-c", "import ddtrace.auto", env=env)
    assert status == 0, stderr
    configs = test_agent_session.get_configurations("instrumentation_source")
    assert configs and configs[-1]["value"] == "manual"
    test_agent_session.clear()

    _, stderr, status, _ = call_program(sys.executable, "-c", "import ddtrace", env=env)
    assert status == 0, stderr
    configs = test_agent_session.get_configurations("instrumentation_source")
    assert not configs, "instrumentation_source should not be set when ddtrace instrumentation is not used"


def test_update_dependencies_event_when_disabled(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"
    env["DD_TELEMETRY_DEPENDENCY_COLLECTION_ENABLED"] = "false"

    # Import httppretty after ddtrace is imported, this ensures that the module is sent in a dependencies event
    # Imports httpretty twice and ensures only one dependency entry is sent
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess("import xmltodict", env=env)
    events = test_agent_session.get_events("app-dependencies-loaded")
    assert len(events) == 0, events


def test_update_dependencies_event_not_stdlib(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    # Import httppretty after ddtrace is imported, this ensures that the module is sent in a dependencies event
    # Imports httpretty twice and ensures only one dependency entry is sent
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(
        """
import sys
import httpretty
del sys.modules["httpretty"]
import httpretty
""",
        env=env,
    )
    assert status == 0, stderr
    deps = test_agent_session.get_dependencies("httpretty")
    assert len(deps) == 1, deps


def test_app_closing_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that app_shutdown() queues and sends an app-closing telemetry request"""
    # Telemetry writer must start before app-closing event is queued
    telemetry_writer.started = True
    # send app closed event
    telemetry_writer.app_shutdown()
    # ensure a valid request body was sent
    events = test_agent_session.get_events("app-closing")
    assert len(events) == 1
    validate_request_body(events[0], {}, "app-closing", 1)


def test_add_integration(telemetry_writer, test_agent_session, mock_time):
    """asserts that add_integration() queues a valid telemetry request"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        # queue integrations
        telemetry_writer.add_integration("integration-t", True, True, "")
        telemetry_writer.add_integration("integration-f", False, False, "terrible failure")
        # send integrations to the agent
        telemetry_writer.periodic(force_flush=True)

        events = test_agent_session.get_events("app-integrations-change")
        # assert integration change telemetry request was sent
        assert len(events) == 1
        # assert that the request had a valid request body
        events[0]["payload"]["integrations"].sort(key=lambda x: x["name"])
        expected_payload = {
            "integrations": [
                {
                    "name": "integration-f",
                    "version": "",
                    "enabled": False,
                    "auto_enabled": False,
                    "compatible": False,
                    "error": "terrible failure",
                },
                {
                    "name": "integration-t",
                    "version": "",
                    "enabled": True,
                    "auto_enabled": True,
                    "compatible": True,
                    "error": "",
                },
            ]
        }
        validate_request_body(events[0], expected_payload, "app-integrations-change")


def test_app_client_configuration_changed_event(telemetry_writer, test_agent_session, mock_time):
    # force periodic call to flush the first app_started call
    telemetry_writer.periodic(force_flush=True)
    """asserts that queuing a configuration sends a valid telemetry request"""
    with override_global_config(dict()):
        telemetry_writer.add_configuration("product_enabled", True, "env_var")
        telemetry_writer.add_configuration("DD_TRACE_PROPAGATION_STYLE_EXTRACT", "datadog", "default")
        telemetry_writer.add_configuration("product_enabled", False, "code")

        telemetry_writer.periodic(force_flush=True)

        events = test_agent_session.get_events("app-client-configuration-change")
        received_configurations = [c for event in events for c in event["payload"]["configuration"]]
        received_configurations.sort(key=lambda c: c["seq_id"])
        assert (
            received_configurations[0]["seq_id"]
            < received_configurations[1]["seq_id"]
            < received_configurations[2]["seq_id"]
        )
        # assert that all configuration values are sent to the agent in the order they were added (by seq_id)
        assert received_configurations[0]["name"] == "product_enabled"
        assert received_configurations[0]["origin"] == "env_var"
        assert received_configurations[0]["value"] is True
        assert received_configurations[1]["name"] == "DD_TRACE_PROPAGATION_STYLE_EXTRACT"
        assert received_configurations[1]["origin"] == "default"
        assert received_configurations[1]["value"] == "datadog"
        assert received_configurations[2]["name"] == "product_enabled"
        assert received_configurations[2]["origin"] == "code"
        assert received_configurations[2]["value"] is False


def test_add_integration_disabled_writer(telemetry_writer, test_agent_session):
    """asserts that add_integration() does not queue an integration when telemetry is disabled"""
    telemetry_writer.disable()

    telemetry_writer.add_integration("integration-name", True, False, "")
    telemetry_writer.periodic(force_flush=True)
    assert len(test_agent_session.get_events("app-integrations-change")) == 0


@pytest.mark.parametrize("mock_status", [300, 400, 401, 403, 500])
def test_send_failing_request(mock_status, telemetry_writer):
    """asserts that a warning is logged when an unsuccessful response is returned by the http client"""

    with override_global_config(dict(_telemetry_dependency_collection=False)):
        # force periodic call to flush the first app_started call
        telemetry_writer.periodic(force_flush=True)
        with httpretty.enabled():
            httpretty.register_uri(httpretty.POST, telemetry_writer._client.url, status=mock_status)
            with mock.patch("ddtrace.internal.telemetry.writer.log") as log:
                # sends failing app-heartbeat event
                telemetry_writer.periodic(force_flush=True)
                # asserts unsuccessful status code was logged
                log.debug.assert_called_with(
                    "Failed to send Instrumentation Telemetry to %s. Response: %s",
                    telemetry_writer._client.url,
                    mock_status,
                )


def test_app_heartbeat_event_periodic(mock_time: mock.Mock, telemetry_writer: Any, test_agent_session: Any) -> None:
    """asserts that we queue/send app-heartbeat when periodc() is called"""
    # Ensure telemetry writer is initialized to send periodic events
    telemetry_writer._is_periodic = True
    telemetry_writer.started = True
    # Assert default telemetry interval is 10 seconds and the expected periodic threshold and counts are set
    assert telemetry_writer.interval == 10
    assert telemetry_writer._periodic_threshold == 5
    assert telemetry_writer._periodic_count == 0

    # Assert next flush contains app-heartbeat event
    for _ in range(telemetry_writer._periodic_threshold):
        telemetry_writer.periodic()
        assert test_agent_session.get_events(mock.ANY, filter_heartbeats=False) == []

    telemetry_writer.periodic()
    heartbeat_events = test_agent_session.get_events("app-heartbeat", filter_heartbeats=False)
    assert len(heartbeat_events) == 1


def test_app_heartbeat_event(mock_time: mock.Mock, telemetry_writer: Any, test_agent_session: Any) -> None:
    """asserts that we queue/send app-heartbeat event every 60 seconds when app_heartbeat_event() is called"""
    # Assert a maximum of one heartbeat is queued per flush
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events(mock.ANY, filter_heartbeats=False)
    assert len(events) > 0


def test_app_product_change_event(mock_time: mock.Mock, telemetry_writer: Any, test_agent_session: Any) -> None:
    """asserts that enabling or disabling an APM Product triggers a valid telemetry request"""

    # Assert that the default product status is disabled
    assert any(telemetry_writer._product_enablement.values()) is False
    # Assert that the product status is first reported in app-started event
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.LLMOBS, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, True)
    assert all(telemetry_writer._product_enablement.values())

    telemetry_writer.periodic(force_flush=True)

    # Assert that there's only an app_started event (since product activation happened before the app started)
    events = test_agent_session.get_events("app-product-change")
    telemetry_writer.periodic(force_flush=True)
    assert not len(events)
    # Assert that unchanged status doesn't generate the event
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events("app-product-change")
    assert not len(events)
    # Assert that product change event is sent when product status changes
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION, False)
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events("app-product-change")
    assert len(events) == 1
    assert events[0]["request_type"] == "app-product-change"
    products = events[0]["payload"]["products"]
    version = _pep440_to_semver()
    assert products == {
        TELEMETRY_APM_PRODUCT.APPSEC.value: {"enabled": False, "version": version},
        TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION.value: {"enabled": False, "version": version},
    }


def validate_request_body(received_body: dict, payload: dict, payload_type: str, seq_id: Optional[int] = None) -> dict:
    """used to test the body of requests received by the testagent"""
    assert len(received_body) == 9
    assert received_body["tracer_time"] == time.time()
    assert received_body["runtime_id"] == get_runtime_id()
    assert received_body["api_version"] == "v2"
    assert received_body["debug"] is False
    if seq_id is not None:
        assert received_body["seq_id"] == seq_id
    assert received_body["application"] == get_application(config.service, config.version, config.env)
    assert received_body["host"] == get_host_info()
    if payload is not None:
        assert received_body["payload"] == payload
    assert received_body["request_type"] == payload_type
    return received_body


def test_telemetry_writer_agent_setup():
    with override_global_config(
        {"_dd_site": "datad0g.com", "_dd_api_key": "foobarkey", "_ci_visibility_agentless_enabled": False}
    ):
        new_telemetry_writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=False)
        assert new_telemetry_writer._enabled
        assert new_telemetry_writer._client._endpoint == "telemetry/proxy/api/v2/apmtelemetry"
        assert "http://" in new_telemetry_writer._client._telemetry_url
        assert ":9126" in new_telemetry_writer._client._telemetry_url
        assert "dd-api-key" not in new_telemetry_writer._client._headers


@pytest.mark.parametrize(
    "env_agentless,arg_agentless,expected_endpoint",
    [
        (True, True, "api/v2/apmtelemetry"),
        (True, False, "telemetry/proxy/api/v2/apmtelemetry"),
        (False, True, "api/v2/apmtelemetry"),
        (False, False, "telemetry/proxy/api/v2/apmtelemetry"),
    ],
)
def test_telemetry_writer_agent_setup_agentless_arg_overrides_env(env_agentless, arg_agentless, expected_endpoint):
    with override_global_config(
        {"_dd_site": "datad0g.com", "_dd_api_key": "foobarkey", "_ci_visibility_agentless_enabled": env_agentless}
    ):
        new_telemetry_writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=arg_agentless)
        # Note: other tests are checking whether values bet set properly, so we're only looking at agentlessness here
        assert new_telemetry_writer._client._endpoint == expected_endpoint


@pytest.mark.subprocess(
    env={"DD_SITE": "datad0g.com", "DD_API_KEY": "foobarkey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
)
def test_telemetry_writer_agentless_setup():
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url == "https://all-http-intake.logs.datad0g.com"
    assert telemetry_writer._client._headers["dd-api-key"] == "foobarkey"


@pytest.mark.subprocess(
    env={"DD_SITE": "datadoghq.eu", "DD_API_KEY": "foobarkey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
)
def test_telemetry_writer_agentless_setup_eu():
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url == "https://instrumentation-telemetry-intake.datadoghq.eu"
    assert telemetry_writer._client._headers["dd-api-key"] == "foobarkey"


@pytest.mark.subprocess(env={"DD_SITE": "datad0g.com", "DD_API_KEY": "", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"})
def test_telemetry_writer_agentless_disabled_without_api_key():
    from ddtrace.internal.telemetry import telemetry_writer

    assert not telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url == "https://all-http-intake.logs.datad0g.com"
    assert "dd-api-key" not in telemetry_writer._client._headers


@pytest.mark.subprocess(env={"DD_SITE": "datad0g.com", "DD_API_KEY": "foobarkey"})
def test_telemetry_writer_is_using_agentless_by_default_if_api_key_is_available():
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url == "https://all-http-intake.logs.datad0g.com"
    assert telemetry_writer._client._headers["dd-api-key"] == "foobarkey"


@pytest.mark.subprocess(env={"DD_API_KEY": "", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "false"})
def test_telemetry_writer_is_using_agent_by_default_if_api_key_is_not_available():
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._enabled
    assert telemetry_writer._client._endpoint == "telemetry/proxy/api/v2/apmtelemetry"
    assert telemetry_writer._client._telemetry_url in ("http://localhost:9126", "http://testagent:9126")
    assert "dd-api-key" not in telemetry_writer._client._headers


def test_otel_config_telemetry(test_agent_session, run_python_code_in_subprocess, tmpdir):
    """
    asserts that telemetry data is submitted for OpenTelemetry configurations
    """

    env = os.environ.copy()
    env["DD_SERVICE"] = "dd_service"
    env["OTEL_SERVICE_NAME"] = "otel_service"
    env["OTEL_LOG_LEVEL"] = "DEBUG"
    env["OTEL_PROPAGATORS"] = "tracecontext"
    env["OTEL_TRACES_SAMPLER"] = "always_on"
    env["OTEL_TRACES_EXPORTER"] = "none"
    env["OTEL_LOGS_EXPORTER"] = "otlp"
    env["OTEL_METRICS_EXPORTER"] = "otlp"
    env["OTEL_RESOURCE_ATTRIBUTES"] = "team=apm,component=web"
    env["OTEL_SDK_DISABLED"] = "true"
    env["OTEL_UNSUPPORTED_CONFIG"] = "value"
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace", env=env)
    assert status == 0, stderr

    configurations = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True, effective=True)}

    assert configurations["DD_SERVICE"] == {"name": "DD_SERVICE", "origin": "env_var", "value": "dd_service"}
    assert configurations["DD_TRACE_DEBUG"] == {"name": "DD_TRACE_DEBUG", "origin": "otel_env_var", "value": "debug"}
    assert configurations["DD_TRACE_PROPAGATION_STYLE_INJECT"] == {
        "name": "DD_TRACE_PROPAGATION_STYLE_INJECT",
        "origin": "otel_env_var",
        "value": "tracecontext",
    }
    assert configurations["DD_TRACE_PROPAGATION_STYLE_EXTRACT"] == {
        "name": "DD_TRACE_PROPAGATION_STYLE_EXTRACT",
        "origin": "otel_env_var",
        "value": "tracecontext",
    }
    assert configurations["DD_TRACE_SAMPLING_RULES"] == {
        "name": "DD_TRACE_SAMPLING_RULES",
        "origin": "otel_env_var",
        "value": "always_on",
    }
    assert configurations["DD_TRACE_ENABLED"] == {
        "name": "DD_TRACE_ENABLED",
        "origin": "otel_env_var",
        "value": "none",
    }
    assert configurations["DD_TAGS"] == {
        "name": "DD_TAGS",
        "origin": "otel_env_var",
        "value": "team=apm,component=web",
    }
    assert configurations["DD_TRACE_OTEL_ENABLED"] == {
        "name": "DD_TRACE_OTEL_ENABLED",
        "origin": "otel_env_var",
        "value": "true",
    }

    env_hiding_metrics = test_agent_session.get_metrics("otel.env.hiding")
    tags = [m["tags"] for m in env_hiding_metrics]
    assert tags == [["config_opentelemetry:otel_service_name", "config_datadog:dd_service"]]

    env_unsupported_metrics = test_agent_session.get_metrics("otel.env.unsupported")
    tags = [m["tags"] for m in env_unsupported_metrics]
    assert tags == [["config_opentelemetry:otel_unsupported_config"]]

    env_invalid_metrics = test_agent_session.get_metrics("otel.env.invalid")
    tags = [m["tags"] for m in env_invalid_metrics]
    assert tags == [["config_opentelemetry:otel_logs_exporter"]]


def test_otel_exporter_otlp_headers_telemetry_omitted(test_agent_session, run_python_code_in_subprocess):
    """The OTEL_EXPORTER_OTLP_*_HEADERS family is excluded from configuration telemetry, while
    non-sensitive OTLP exporter configurations are still reported.
    """
    code = """
# most configurations are reported when ddtrace.auto is imported
import ddtrace.auto
# importing opentelemetry triggers reporting of the OTLP exporter configurations
import opentelemetry
    """

    # Distinct, recognizable sentinels per OTLP header variant.
    sentinels = [
        "SENTINEL_OTLP_BASE",
        "SENTINEL_OTLP_TRACES",
        "SENTINEL_OTLP_METRICS",
        "SENTINEL_OTLP_LOGS",
    ]

    env = os.environ.copy()
    env["OTEL_EXPORTER_OTLP_HEADERS"] = "dd-api-key=SENTINEL_OTLP_BASE"
    env["OTEL_EXPORTER_OTLP_TRACES_HEADERS"] = "dd-api-key=SENTINEL_OTLP_TRACES"
    env["OTEL_EXPORTER_OTLP_METRICS_HEADERS"] = "dd-api-key=SENTINEL_OTLP_METRICS"
    env["OTEL_EXPORTER_OTLP_LOGS_HEADERS"] = "dd-api-key=SENTINEL_OTLP_LOGS"
    # Non-sensitive OTLP exporter configurations that must still be reported.
    env["OTEL_EXPORTER_OTLP_ENDPOINT"] = "http://localhost:4318"
    env["_DD_INSTRUMENTATION_TELEMETRY_TESTS_FORCE_APP_STARTED"] = "true"

    _, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    configurations = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True, effective=True)}
    assert configurations, "no configuration telemetry was reported"

    # Invariant: no OTLP header sentinel appears in any reported configuration value.
    for cfg in configurations.values():
        for sentinel in sentinels:
            assert sentinel not in str(cfg["value"]), cfg

    # Python omits the OTLP header family entirely.
    for name in (
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
        "OTEL_EXPORTER_OTLP_LOGS_HEADERS",
    ):
        assert name not in configurations, configurations.get(name)

    # Non-sensitive OTLP exporter configurations are still reported.
    assert configurations["OTEL_EXPORTER_OTLP_ENDPOINT"] == {
        "name": "OTEL_EXPORTER_OTLP_ENDPOINT",
        "origin": "env_var",
        "value": "http://localhost:4318",
    }
    # Sibling non-sensitive exporter configs (collected at import) remain present.
    assert "OTEL_EXPORTER_OTLP_PROTOCOL" in configurations
    assert "OTEL_EXPORTER_OTLP_TIMEOUT" in configurations


def test_dd_api_key_app_key_telemetry_omitted(telemetry_writer, test_agent_session):
    """DD_API_KEY and DD_APP_KEY values are excluded from configuration telemetry.

    Uses the in-process telemetry writer (forced non-agentless) because setting DD_API_KEY would
    otherwise switch a subprocess's telemetry client into agentless mode and divert it from the
    test agent.
    """
    from ddtrace.internal.telemetry import get_config

    with mock.patch.dict(
        os.environ,
        {"DD_API_KEY": "SENTINEL_DD_API_KEY", "DD_APP_KEY": "SENTINEL_DD_APP_KEY"},
    ):
        # Read each sensitive key the way settings do; the value must not be queued for telemetry.
        assert get_config("DD_API_KEY") == "SENTINEL_DD_API_KEY"
        assert get_config("DD_APP_KEY") == "SENTINEL_DD_APP_KEY"
        # A non-sensitive control config is still reported, proving reporting is otherwise active.
        get_config("DD_SITE", "datadoghq.com")

    queued = list(telemetry_writer._queued_configs)
    queued_names = {c["name"] for c in queued}
    assert "DD_API_KEY" not in queued_names, queued
    assert "DD_APP_KEY" not in queued_names, queued
    for cfg in queued:
        assert "SENTINEL_DD_API_KEY" not in str(cfg["value"]), cfg
        assert "SENTINEL_DD_APP_KEY" not in str(cfg["value"]), cfg
    # Sanity check: the non-sensitive control config was reported.
    assert "DD_SITE" in queued_names, queued


def test_add_error_log(mock_time, telemetry_writer, test_agent_session):
    """Test add_integration_error_log functionality with real stack trace"""
    try:
        import json

        json.loads("{invalid: json,}")
    except Exception as e:
        telemetry_writer.add_error_log("Test error message", e)
        telemetry_writer.periodic(force_flush=True)

        log_events = test_agent_session.get_events("logs")
        assert len(log_events) == 1

        logs = log_events[0]["payload"]["logs"]
        assert len(logs) == 1

        log_entry = logs[0]
        assert log_entry["level"] == TELEMETRY_LOG_LEVEL.ERROR.value
        assert log_entry["message"] == "Test error message"
        assert log_entry["tags"] == "error_type:jsondecodeerror"

        stack_trace = log_entry["stack_trace"]
        expected_lines = [
            "Traceback (most recent call last):",
            "<REDACTED>",  # User code gets redacted
            '  File "json/__init__.py',
            "    return _default_decoder.decode(s)",
            '  File "json/decoder.py"',
            "    obj, end = self.raw_decode(s, idx=_w(s, 0).end())",
            '  File "json/decoder.py"',
            "    obj, end = self.scan_once(s, idx)",
            "json.decoder.JSONDecodeError: <REDACTED>",
        ]
        for expected_line in expected_lines:
            assert expected_line in stack_trace


def test_add_error_log_large_stack(mock_time, telemetry_writer, test_agent_session):
    """Test add_integration_error_log functionality with real stack trace"""
    try:

        def _(n):
            if n == 200:
                raise ValueError("Test exception for large stack trace")
            return _(n + 1)

        _(0)
    except Exception as e:
        telemetry_writer.add_error_log("Test error message", e)
        telemetry_writer.periodic(force_flush=True)

        log_events = test_agent_session.get_events("logs")
        assert len(log_events) == 1

        logs = log_events[0]["payload"]["logs"]
        assert len(logs) == 1

        log_entry = logs[0]
        assert log_entry["level"] == TELEMETRY_LOG_LEVEL.ERROR.value
        assert log_entry["message"] == "Test error message"
        assert log_entry["tags"] == "error_type:valueerror"

        stack_trace = log_entry["stack_trace"]
        expected_lines = """Traceback (most recent call last):
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
  <REDACTED>
    <REDACTED>
builtins.ValueError: <REDACTED>"""
        assert stack_trace == expected_lines


def test_add_integration_error_log_with_log_collection_disabled(mock_time, telemetry_writer, test_agent_session):
    """Test that add_integration_error_log respects LOG_COLLECTION_ENABLED setting"""
    original_value = telemetry_config.LOG_COLLECTION_ENABLED
    try:
        telemetry_config.LOG_COLLECTION_ENABLED = False

        try:
            raise ValueError("Test exception")
        except ValueError as e:
            telemetry_writer.add_error_log("Test error message", e)
            telemetry_writer.periodic(force_flush=True)

            log_events = test_agent_session.get_events("logs")
            assert len(log_events) == 0
    finally:
        telemetry_config.LOG_COLLECTION_ENABLED = original_value


def test_error_log_handler_strips_skipped_suffix(mock_time, telemetry_writer, test_agent_session):
    """Test that DDTelemetryErrorHandler strips [x skipped] suffix from error messages"""
    import logging

    ddtrace_logger = logging.getLogger("ddtrace")

    ddtrace_logger.error("Error message [123 skipped]")
    telemetry_writer.periodic(force_flush=True)

    log_events = test_agent_session.get_events("logs")
    assert len(log_events) == 1

    logs = log_events[0]["payload"]["logs"]
    assert len(logs) == 1
    assert logs[0]["message"] == "Error message"

    test_agent_session.clear()

    ddtrace_logger.error("Normal error message [something]")
    telemetry_writer.periodic(force_flush=True)

    log_events = test_agent_session.get_events("logs")
    assert len(log_events) == 1

    logs = log_events[0]["payload"]["logs"]
    assert len(logs) == 1
    assert logs[0]["message"] == "Normal error message [something]"


@pytest.mark.parametrize(
    "filename, result",
    [
        ("/path/to/file.py", "<REDACTED>"),
        ("/path/to/ddtrace/contrib/flask/file.py", "<REDACTED>"),
        ("/path/to/lib/python3.13/site-packages/ddtrace/_trace/tracer.py", "ddtrace/_trace/tracer.py"),
        ("/path/to/lib/python3.13/site-packages/requests/api.py", "requests/api.py"),
        (
            "/path/to/python@3.13/3.13.1/Frameworks/Python.framework/Versions/3.13/lib/python3.13/json/__init__.py",
            "json/__init__.py",
        ),
    ],
)
def test_redact_filename(filename, result):
    """Test file redaction logic"""
    writer = TelemetryWriter(is_periodic=False)
    assert writer._format_file_path(filename) == result


def test_telemetry_writer_multiple_sources_config(telemetry_writer, test_agent_session):
    """Test that telemetry data is submitted for multiple sources with increasing seq_id"""

    telemetry_writer.add_configuration("DD_SERVICE", "unamed_python_service", "default")
    telemetry_writer.add_configuration("DD_SERVICE", "otel_service", "otel_env_var")
    telemetry_writer.add_configuration("DD_SERVICE", "dd_service", "env_var")
    telemetry_writer.add_configuration("DD_SERVICE", "monkey", "code")
    telemetry_writer.add_configuration("DD_SERVICE", "baboon", "remote_config")
    telemetry_writer.add_configuration("DD_SERVICE", "baboon", "fleet_stable_config")

    telemetry_writer.periodic(force_flush=True)

    configs = test_agent_session.get_configurations(name="DD_SERVICE", remove_seq_id=False, effective=False)
    assert len(configs) == 6, configs

    sorted_configs = sorted(configs, key=lambda x: x["seq_id"])
    assert sorted_configs[0]["value"] == "unamed_python_service"
    assert sorted_configs[0]["origin"] == "default"
    assert sorted_configs[0]["seq_id"] == 1

    assert sorted_configs[1]["value"] == "otel_service"
    assert sorted_configs[1]["origin"] == "otel_env_var"
    assert sorted_configs[1]["seq_id"] == 2

    assert sorted_configs[2]["value"] == "dd_service"
    assert sorted_configs[2]["origin"] == "env_var"
    assert sorted_configs[2]["seq_id"] == 3

    assert sorted_configs[3]["value"] == "monkey"
    assert sorted_configs[3]["origin"] == "code"
    assert sorted_configs[3]["seq_id"] == 4

    assert sorted_configs[4]["value"] == "baboon"
    assert sorted_configs[4]["origin"] == "remote_config"
    assert sorted_configs[4]["seq_id"] == 5

    assert sorted_configs[5]["value"] == "baboon"
    assert sorted_configs[5]["origin"] == "fleet_stable_config"
    assert sorted_configs[5]["seq_id"] == 6


def test_report_configuration_walks_ddconfig(telemetry_writer, test_agent_session, monkeypatch):
    """report_configuration() reports every public, non-sensitive item of a DDConfig with its
    resolved value, source and config_id, and skips private and sensitive items entirely.
    """
    monkeypatch.setenv("DD_TEST_SYNTHETIC_PUBLIC_SETTING", "from_env")
    monkeypatch.setenv("DD_TEST_SYNTHETIC_BOOL_SETTING", "true")
    monkeypatch.setenv("DD_TEST_SYNTHETIC_FLOAT_SETTING", "1.5")

    with (
        mock.patch.dict(settings_core.FLEET_CONFIG, {"DD_TEST_SYNTHETIC_FLEET_SETTING": "from_fleet"}),
        mock.patch.dict(settings_core.FLEET_CONFIG_IDS, {"DD_TEST_SYNTHETIC_FLEET_SETTING": "config-id-123"}),
    ):
        synthetic_config = _SyntheticDDConfig()

    with mock.patch.object(
        ddtrace.internal.telemetry,
        "SENSITIVE_CONFIGURATIONS",
        frozenset({"DD_TEST_SYNTHETIC_SENSITIVE_SETTING"}),
    ):
        ddtrace.internal.telemetry.report_configuration(synthetic_config)

    telemetry_writer.periodic(force_flush=True)
    reported = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True)}

    assert reported["DD_TEST_SYNTHETIC_PUBLIC_SETTING"]["origin"] == "env_var"
    assert reported["DD_TEST_SYNTHETIC_PUBLIC_SETTING"]["value"] == "from_env"

    assert reported["DD_TEST_SYNTHETIC_FLEET_SETTING"]["origin"] == "fleet_stable_config"
    assert reported["DD_TEST_SYNTHETIC_FLEET_SETTING"]["value"] == "from_fleet"
    assert reported["DD_TEST_SYNTHETIC_FLEET_SETTING"]["config_id"] == "config-id-123"

    assert "DD_TEST_SYNTHETIC_PRIVATE_SETTING" not in reported
    assert "DD_TEST_SYNTHETIC_SENSITIVE_SETTING" not in reported

    # Values are reported using the DDConfig item's declared type, not as raw strings.
    assert reported["DD_TEST_SYNTHETIC_BOOL_SETTING"]["value"] is True
    assert reported["DD_TEST_SYNTHETIC_FLOAT_SETTING"]["value"] == 1.5


def test_get_config_reports_all_sources_by_precedence(telemetry_writer, test_agent_session, monkeypatch):
    """get_config() reports telemetry for every source that supplies a value and returns the
    value from the highest-precedence source: fleet stable config > env var > local stable
    config > default.
    """
    name = "DD_TEST_SYNTHETIC_GET_CONFIG_SETTING"

    assert ddtrace.internal.telemetry.get_config(name, "default_value") == "default_value"

    with mock.patch.dict(ddtrace.internal.telemetry.LOCAL_CONFIG, {name: "local_value"}):
        assert ddtrace.internal.telemetry.get_config(name, "default_value") == "local_value"

    monkeypatch.setenv(name, "env_value")
    with mock.patch.dict(ddtrace.internal.telemetry.LOCAL_CONFIG, {name: "local_value"}):
        assert ddtrace.internal.telemetry.get_config(name, "default_value") == "env_value"

    with (
        mock.patch.dict(ddtrace.internal.telemetry.LOCAL_CONFIG, {name: "local_value"}),
        mock.patch.dict(ddtrace.internal.telemetry.FLEET_CONFIG, {name: "fleet_value"}),
        mock.patch.dict(ddtrace.internal.telemetry.FLEET_CONFIG_IDS, {name: "config-id-456"}),
    ):
        assert ddtrace.internal.telemetry.get_config(name, "default_value") == "fleet_value"

    telemetry_writer.periodic(force_flush=True)
    reported = test_agent_session.get_configurations(name=name, remove_seq_id=False, effective=False)
    origins = {c["origin"] for c in reported}
    assert origins == {"default", "local_stable_config", "env_var", "fleet_stable_config"}

    fleet_entry = next(c for c in reported if c["origin"] == "fleet_stable_config")
    assert fleet_entry["value"] == "fleet_value"
    assert fleet_entry["config_id"] == "config-id-456"


def test_get_config_respects_aliases_and_sensitive_configurations(telemetry_writer, test_agent_session, monkeypatch):
    """get_config() honors registered aliases of the canonical env var name and never reports
    telemetry for configurations marked sensitive, regardless of which source supplies them.
    """
    canonical = "DD_TEST_SYNTHETIC_CANONICAL_SETTING"
    alias = "DD_TEST_SYNTHETIC_LEGACY_ALIAS"
    monkeypatch.setenv(alias, "aliased_value")

    with mock.patch.dict(ddtrace.internal.telemetry.CONFIGURATION_ALIASES, {canonical: [alias]}):
        assert ddtrace.internal.telemetry.get_config(canonical, "default_value") == "aliased_value"

    telemetry_writer.periodic(force_flush=True)
    reported = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True)}
    assert reported[canonical]["origin"] == "env_var"
    assert reported[canonical]["value"] == "aliased_value"

    sensitive_name = "DD_TEST_SYNTHETIC_SENSITIVE_GET_CONFIG_SETTING"
    monkeypatch.setenv(sensitive_name, "leaked_value")
    with mock.patch.object(
        ddtrace.internal.telemetry,
        "SENSITIVE_CONFIGURATIONS",
        frozenset({sensitive_name}),
    ):
        assert ddtrace.internal.telemetry.get_config(sensitive_name, "default_value") == "leaked_value"

    telemetry_writer.periodic(force_flush=True)
    reported = {c["name"]: c for c in test_agent_session.get_configurations(remove_seq_id=True)}
    assert sensitive_name not in reported


@pytest.mark.subprocess(env={"DD_INTERNAL_TELEMETRY_DEBUG_ENABLED": "true"})
def test_telemetry_debug_enabled_by_telemetry_env_var():
    """Telemetry debug mode is enabled only by DD_INTERNAL_TELEMETRY_DEBUG_ENABLED, not DD_TRACE_DEBUG."""
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._debug is True


@pytest.mark.subprocess(env={"DD_TRACE_DEBUG": "true"}, err=None)
def test_telemetry_debug_not_enabled_by_tracer_debug():
    """Setting DD_TRACE_DEBUG must not enable telemetry debug mode."""
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._debug is False
