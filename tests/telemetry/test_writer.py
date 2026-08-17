import os
import sys
import sysconfig
from typing import Any
from typing import Optional
from unittest import mock

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


@pytest.fixture(autouse=True)
def _no_inherited_api_key(monkeypatch):
    """Keep subprocess telemetry writers in non-agentless mode.

    A ``DD_API_KEY`` present in the test environment is inherited by the subprocesses these tests
    spawn (``os.environ.copy()``) and flips their telemetry writer into agentless mode, diverting
    requests to the Datadog intake instead of the local test agent. Tests that genuinely need an
    api key set it explicitly via the subprocess marker env / mock.patch.dict, which overrides
    this removal.
    """
    monkeypatch.delenv("DD_API_KEY", raising=False)


def _to_config_str(value):
    """Mirror the native worker's configuration value serialization.

    The native TelemetryWorker serializes each configuration ``value`` (see
    ``TelemetryWriter.add_configuration`` / ``_config_value_to_str``: ``None`` stays ``None``
    (a JSON ``null``), booleans become lowercase ``"true"``/``"false"``, everything else
    ``str(value)`` after dict/list flattening). So tests that assert typed values
    (bool/int/float/None/list) must compare against this wire form.
    """
    if value is None:
        return None
    if isinstance(value, dict):
        return ",".join(":".join((k, str(v))) for k, v in value.items())
    if isinstance(value, (set, frozenset)):
        return ",".join(sorted(str(v) for v in value))
    if isinstance(value, (list, tuple)):
        return ",".join(str(v) for v in value)
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


@pytest.mark.parametrize(
    "env_var,value,expected_value",
    [
        ("DD_APPSEC_SCA_ENABLED", "true", True),
        ("DD_APPSEC_SCA_ENABLED", "True", True),
        ("DD_APPSEC_SCA_ENABLED", "1", True),
        ("DD_APPSEC_SCA_ENABLED", "false", False),
        ("DD_APPSEC_SCA_ENABLED", "False", False),
        ("DD_APPSEC_SCA_ENABLED", "0", False),
    ],
)
def test_app_started_event_configuration_override_asm(
    test_agent_session, run_python_code_in_subprocess, env_var, value, expected_value
):
    """asserts that asm configuration value is changed and queues a valid telemetry request"""
    env = os.environ.copy()
    env["DD_APPSEC_ENABLED"] = "true"
    env[env_var] = value
    # Keep the subprocess writer non-agentless (a stray DD_API_KEY would route to intake).
    env.pop("DD_API_KEY", None)
    _, stderr, status, _ = run_python_code_in_subprocess("import ddtrace.auto", env=env)
    assert status == 0, stderr

    configuration = test_agent_session.get_configurations(name=env_var, remove_seq_id=True, effective=True)
    assert len(configuration) == 1, configuration
    assert configuration[0] == {"name": env_var, "origin": "env_var", "value": _to_config_str(expected_value)}


def test_app_started_event(telemetry_writer, test_agent_session, mock_time):
    """asserts that app-started is emitted exactly once with a valid body"""
    with override_global_config(dict(_telemetry_dependency_collection=False)):
        # The native worker emits app-started eagerly on start() as its own request, so we assert
        # on the app-started event content (it appears exactly once) rather than the total request
        # count, which now also includes the eager app-started + heartbeat/closing lifecycle events.
        telemetry_writer.periodic(force_flush=True)
        app_started_events = test_agent_session.get_events("app-started")
        assert len(app_started_events) == 1
        validate_request_body(app_started_events[0], None, "app-started")
        # app-started carries at least a configuration list (products may be null when nothing is
        # activated before start, since the native worker reports activations as app-product-change).
        assert app_started_events[0]["payload"].get("configuration")

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


def test_app_started_forwards_process_tags(telemetry_writer, test_agent_session, mock_time):
    """The native worker must forward application.process_tags (svc.user/svc.auto, entrypoint.*).

    Regression guard for an optional field the native Application drops easily: validate_request_body
    only compares application keys already present in the received body, so a dropped process_tags
    would pass silently there. process tags are enabled by default, so every request's application
    must carry the exact string computed on the Python side.
    """
    from ddtrace.internal import process_tags
    from ddtrace.internal.settings.process_tags import process_tags_config

    if not process_tags_config.enabled:
        pytest.skip("process tags are disabled")

    telemetry_writer.periodic(force_flush=True)
    app_started_events = test_agent_session.get_events("app-started")
    assert len(app_started_events) == 1
    application = app_started_events[0]["application"]
    assert application.get("process_tags") == process_tags.process_tags
    # svc.user (user-provided service) or svc.auto (inferred) is always present in the tag string.
    assert "svc.user:" in application["process_tags"] or "svc.auto:" in application["process_tags"]


def test_update_dependencies_event(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    # app-started events are sent 10 seconds after ddtrace imported, this configuration overrides this
    # behavior to force the app-started event to be queued immediately

    # Import httppretty after ddtrace is imported, this ensures that the module is sent in a dependencies event
    # Imports httpretty twice and ensures only one dependency entry is sent
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess("import xmltodict", env=env)
    assert status == 0, stderr
    deps = test_agent_session.get_dependencies("xmltodict")
    assert len(deps) == 1, deps


_MINI_DJANGO_APP = """
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
    %(bootstrap)s

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

# What gunicorn/uwsgi do: build the WSGI application, which constructs a BaseHandler.
_SERVING_BOOTSTRAP = """from django.core.wsgi import get_wsgi_application
    get_wsgi_application()"""

# What a Celery or dramatiq worker does: django.setup() and nothing else. Not a management
# command -- Django's own check_url_config imports the URLconf whenever system checks run, so
# most manage.py invocations import it with or without ddtrace.
_WORKER_BOOTSTRAP = """import django
    django.setup()
    assert this not in __import__('sys').modules, 'django.setup() imported the URLconf'"""


def test_endpoint_discovery_event(test_agent_session, ddtrace_run_python_code_in_subprocess):
    env = os.environ.copy()
    mini_django_app = _MINI_DJANGO_APP % {"bootstrap": _SERVING_BOOTSTRAP}

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
    # The mini_app view has no @require_http_methods, so its method is unknown/unconstrained.
    # libdatadog's ``Method::Other`` serializes to "*" (the value the app-endpoints OpenAPI spec
    # uses for the any-method concept), matching the old Python writer.
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


def test_endpoint_discovery_message_limit(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """DD_API_SECURITY_ENDPOINT_COLLECTION_MESSAGE_LIMIT caps a payload, it does not drop endpoints.

    The limit is handed to the native worker, which splits app-endpoints across payloads. Only the
    first may set is_first (the backend replaces its endpoint set on a first payload and merges on
    the rest), and every endpoint has to arrive across the chunks.
    """
    env = os.environ.copy()
    env["DD_API_SECURITY_ENDPOINT_COLLECTION_MESSAGE_LIMIT"] = "3"

    code = """
from ddtrace.internal.endpoints import endpoint_collection
from ddtrace.internal.telemetry import telemetry_writer

for i in range(7):
    endpoint_collection.add_endpoint(method="GET", path="/r%d" % i)

# One flush per chunk: each carries at most the configured limit, the rest stays queued.
for _ in range(4):
    telemetry_writer.periodic(force_flush=True)
"""
    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(code, env=env)
    assert status == 0, stderr

    events = test_agent_session.get_events("app-endpoints")
    chunks = [(e["payload"]["is_first"], len(e["payload"]["endpoints"])) for e in events]
    assert chunks, "no app-endpoints payload was sent"
    assert all(count <= 3 for _, count in chunks), chunks
    # Exactly one is_first across every chunk - what system-tests' test_single_is_first asserts.
    assert sum(1 for is_first, _ in chunks if is_first) == 1, chunks

    paths = {e["path"] for event in events for e in event["payload"]["endpoints"]}
    assert paths == {"/r%d" % i for i in range(7)}, paths


def test_endpoint_discovery_skipped_without_http_handler(test_agent_session, ddtrace_run_python_code_in_subprocess):
    """A process that never builds a request handler must not import the URLconf.

    Reading resolver.url_patterns imports ROOT_URLCONF and, through include(), every view module behind it. A Celery
    or dramatiq worker would otherwise load that whole import closure for nothing, which cost one reporter 154MB of
    RSS per worker.
    """
    env = os.environ.copy()

    mini_django_app = _MINI_DJANGO_APP % {"bootstrap": _WORKER_BOOTSTRAP}

    _, stderr, status, _ = ddtrace_run_python_code_in_subprocess(mini_django_app, env=env)
    assert status == 0, stderr
    deps = test_agent_session.get_dependencies("django")
    assert len(deps) == 1, deps

    assert test_agent_session.get_events("app-endpoints") == []


def test_instrumentation_source_config(
    test_agent_session, ddtrace_run_python_code_in_subprocess, run_python_code_in_subprocess
):
    env = os.environ.copy()

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
    # The worker must actually be started (app-started emitted) before app-closing is meaningful.
    # app-started is deferred out of enable(), so start it explicitly rather than just flipping the
    # ``started`` flag (which would leave the native worker un-started and emit no app-closing).
    telemetry_writer.app_started()
    # send app closed event
    telemetry_writer.app_shutdown()
    # ensure a valid app-closing request body was sent. The native worker's shutdown/rebuild
    # lifecycle (incl. the test-session token rebuild) may surface more than one app-closing, so
    # assert at least one was sent and that it has a valid body. The app-closing unit payload
    # serializes with no "payload" key (the harness defaults it to {}), and seq_id is owned by the
    # native worker, so we don't pin it.
    events = test_agent_session.get_events("app-closing")
    assert len(events) >= 1
    validate_request_body(events[0], {}, "app-closing")


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
                    "version": None,
                    "enabled": False,
                    "auto_enabled": False,
                    "compatible": False,
                    "error": "terrible failure",
                },
                {
                    "name": "integration-t",
                    "version": None,
                    "enabled": True,
                    "auto_enabled": True,
                    "compatible": True,
                    "error": None,
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

        # Other components report their own configuration into the same change stream, so assert
        # that the configs THIS test queued appear (as a subsequence) in the order they were added
        # — by ascending seq_id — with values stringified by the native worker.
        added_in_order = [
            ("product_enabled", "env_var", "true"),
            ("DD_TRACE_PROPAGATION_STYLE_EXTRACT", "default", "datadog"),
            ("product_enabled", "code", "false"),
        ]
        received_in_order = iter((c["name"], c["origin"], c["value"]) for c in received_configurations)
        assert all(cfg in received_in_order for cfg in added_in_order), received_configurations


def test_add_integration_disabled_writer(telemetry_writer, test_agent_session):
    """asserts that add_integration() does not queue an integration when telemetry is disabled"""
    telemetry_writer.disable()

    telemetry_writer.add_integration("integration-name", True, False, "")
    telemetry_writer.periodic(force_flush=True)
    assert len(test_agent_session.get_events("app-integrations-change")) == 0


# NOTE: ``test_send_failing_request`` was removed. It exercised Python-side HTTP retry/error
# logging via httpretty + ``telemetry_writer._client``. Transport (including failure handling
# and logging of unsuccessful responses) now lives in the libdd-telemetry Rust crate, so it can
# no longer be intercepted by httpretty from Python and is covered on the native side.

# NOTE: ``test_app_heartbeat_event_periodic`` was removed. It exercised the Python-side
# heartbeat-gating counters (``_is_periodic`` / ``interval`` / ``_periodic_threshold`` /
# ``_periodic_count``), which no longer exist — the native worker self-schedules heartbeats.
# ``test_app_heartbeat_event`` below still covers that heartbeats are emitted.


def test_app_heartbeat_event(mock_time: mock.Mock, telemetry_writer: Any, test_agent_session: Any) -> None:
    """asserts that we queue/send app-heartbeat event every 60 seconds when app_heartbeat_event() is called"""
    # Assert a maximum of one heartbeat is queued per flush
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events(mock.ANY, filter_heartbeats=False)
    assert len(events) > 0


def test_app_product_change_event(mock_time: mock.Mock, telemetry_writer: Any, test_agent_session: Any) -> None:
    """asserts that enabling or disabling an APM Product triggers a valid telemetry request"""

    # Product enablement state is tracked inside the native worker. app-started is deferred until
    # the first flush (so it carries the full startup configuration), so product activations that
    # happen before that flush are folded into the app-started payload — matching the pre-native
    # writer. Activations afterwards are emitted as their own ``app-product-change`` events; an
    # activation that does not change a product's status produces no event.
    version = _pep440_to_semver()

    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.LLMOBS, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, True)

    telemetry_writer.periodic(force_flush=True)

    # These activations happened before app-started (deferred to this first flush), so they are
    # carried by the app-started payload rather than a separate app-product-change event.
    app_started_events = test_agent_session.get_events("app-started")
    assert len(app_started_events) == 1, app_started_events
    products = app_started_events[0]["payload"]["products"]
    assert products == {
        TELEMETRY_APM_PRODUCT.LLMOBS.value: {"enabled": True, "version": version, "error": None},
        TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION.value: {"enabled": True, "version": version, "error": None},
        TELEMETRY_APM_PRODUCT.PROFILER.value: {"enabled": True, "version": version, "error": None},
        TELEMETRY_APM_PRODUCT.APPSEC.value: {"enabled": True, "version": version, "error": None},
    }
    test_agent_session.clear()

    # The native worker marks a product pending on every ``product_activated`` call (it does not
    # diff against the previous status), so re-activating an already-enabled product re-emits it.
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.PROFILER, True)
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events("app-product-change")
    assert len(events) == 1
    assert events[0]["payload"]["products"] == {
        TELEMETRY_APM_PRODUCT.PROFILER.value: {"enabled": True, "version": version, "error": None},
    }
    test_agent_session.clear()

    # Assert that product change event is sent when product status changes
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.APPSEC, False)
    telemetry_writer.product_activated(TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION, False)
    telemetry_writer.periodic(force_flush=True)
    events = test_agent_session.get_events("app-product-change")
    assert len(events) == 1
    assert events[0]["request_type"] == "app-product-change"
    products = events[0]["payload"]["products"]
    assert products == {
        TELEMETRY_APM_PRODUCT.APPSEC.value: {"enabled": False, "version": version, "error": None},
        TELEMETRY_APM_PRODUCT.DYNAMIC_INSTRUMENTATION.value: {"enabled": False, "version": version, "error": None},
    }


def validate_request_body(received_body: dict, payload: dict, payload_type: str, seq_id: Optional[int] = None) -> dict:
    """used to test the body of requests received by the testagent"""
    # The native worker serializes a fixed set of 8 top-level keys. Unlike the old Python
    # body, there is no ``debug`` key anymore.
    assert set(received_body.keys()) == {
        "api_version",
        "tracer_time",
        "runtime_id",
        "seq_id",
        "application",
        "host",
        "request_type",
        "payload",
    }
    # tracer_time is stamped by the native worker (Rust), so it cannot be mocked from
    # Python (mock_time) — just sanity-check it is a positive epoch-seconds integer.
    assert isinstance(received_body["tracer_time"], int) and received_body["tracer_time"] > 0
    assert received_body["runtime_id"] == get_runtime_id()
    assert received_body["api_version"] == "v2"
    if seq_id is not None:
        assert received_body["seq_id"] == seq_id
    # The wire body omits empty/None application + host fields (serde skip_serializing_if),
    # so only compare against the fields actually present in the received body.
    expected_application = get_application(config.service, config.version, config.env)
    assert received_body["application"] == {
        k: v for k, v in expected_application.items() if k in received_body["application"]
    }
    expected_host = get_host_info()
    assert received_body["host"] == {k: v for k, v in expected_host.items() if k in received_body["host"]}
    if payload is not None:
        assert received_body["payload"] == payload
    assert received_body["request_type"] == payload_type
    return received_body


def test_telemetry_writer_agent_setup():
    with override_global_config(
        {"_dd_site": "datad0g.com", "_dd_api_key": "foobarkey", "_ci_visibility_agentless_enabled": False}
    ):
        new_telemetry_writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=False)
        # Transport now lives in the native worker; the Python-visible decision is the
        # ``_agentless`` flag. Agent mode -> _agentless is False (telemetry POSTed to the
        # trace agent proxy by the native worker).
        assert new_telemetry_writer._enabled
        assert new_telemetry_writer._agentless is False


def test_identity_refresh_rebuilds_native_worker():
    """Same rebuild as after a fork: the native worker bakes in get_runtime_id() at construction."""
    with override_global_config(
        {"_dd_site": "datad0g.com", "_dd_api_key": "foobarkey", "_ci_visibility_agentless_enabled": False}
    ):
        writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=False)
        assert writer._worker is not None

        writer._on_identity_refresh("some-new-runtime-id")

        assert writer._worker is None
        assert writer.started is False


@pytest.mark.subprocess(
    env={"DD_SITE": "datad0g.com", "DD_API_KEY": "foobarkey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "false"}
)
def test_identity_refresh_wired_to_runtime_id_change():
    """Drives the refresh through runtime.refresh_identity() instead of calling
    _on_identity_refresh directly (as the test above does), so a dropped
    on_runtime_id_change() subscription would actually fail this.
    """
    from ddtrace.internal import runtime
    import ddtrace.internal.telemetry

    writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=False)
    assert writer._worker is not None

    runtime.refresh_identity()

    assert writer._worker is None
    assert writer.started is False


@pytest.mark.parametrize(
    "env_agentless,arg_agentless",
    [
        (True, True),
        (True, False),
        (False, True),
        (False, False),
    ],
)
def test_telemetry_writer_agent_setup_agentless_arg_overrides_env(env_agentless, arg_agentless):
    with override_global_config(
        {"_dd_site": "datad0g.com", "_dd_api_key": "foobarkey", "_ci_visibility_agentless_enabled": env_agentless}
    ):
        new_telemetry_writer = ddtrace.internal.telemetry.TelemetryWriter(agentless=arg_agentless)
        # The explicit ``agentless`` argument always wins over the env-derived value.
        assert new_telemetry_writer._agentless is arg_agentless


@pytest.mark.subprocess(
    env={"DD_SITE": "datad0g.com", "DD_API_KEY": "foobarkey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
)
def test_telemetry_writer_agentless_setup():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer
    from ddtrace.internal.telemetry.writer import _agentless_endpoint_url

    assert telemetry_writer._enabled
    assert telemetry_writer._agentless is True
    # The api key is now applied as a header inside the native worker; assert via config.
    assert config._dd_api_key == "foobarkey"
    assert _agentless_endpoint_url(config._dd_site) == "https://all-http-intake.logs.datad0g.com"


@pytest.mark.subprocess(
    env={"DD_SITE": "datadoghq.eu", "DD_API_KEY": "foobarkey", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"}
)
def test_telemetry_writer_agentless_setup_eu():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer
    from ddtrace.internal.telemetry.writer import _agentless_endpoint_url

    assert telemetry_writer._enabled
    assert telemetry_writer._agentless is True
    assert config._dd_api_key == "foobarkey"
    assert _agentless_endpoint_url(config._dd_site) == "https://instrumentation-telemetry-intake.datadoghq.eu"


@pytest.mark.subprocess(env={"DD_SITE": "datad0g.com", "DD_API_KEY": "", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "true"})
def test_telemetry_writer_agentless_disabled_without_api_key():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer

    # Agentless requested but no api key -> telemetry is disabled.
    assert not telemetry_writer._enabled
    assert telemetry_writer._agentless is True
    assert config._dd_api_key in (None, "")


@pytest.mark.subprocess(env={"DD_SITE": "datad0g.com", "DD_API_KEY": "foobarkey"})
def test_telemetry_writer_is_using_agentless_by_default_if_api_key_is_available():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer
    from ddtrace.internal.telemetry.writer import _agentless_endpoint_url

    # When an api key is present (and agentless not explicitly disabled) the writer defaults
    # to agentless mode.
    assert telemetry_writer._enabled
    assert telemetry_writer._agentless is True
    assert config._dd_api_key == "foobarkey"
    assert _agentless_endpoint_url(config._dd_site) == "https://all-http-intake.logs.datad0g.com"


@pytest.mark.subprocess(env={"DD_API_KEY": "", "DD_CIVISIBILITY_AGENTLESS_ENABLED": "false"})
def test_telemetry_writer_is_using_agent_by_default_if_api_key_is_not_available():
    from ddtrace import config
    from ddtrace.internal.telemetry import telemetry_writer

    # No api key and agentless disabled -> agent mode (telemetry goes to the trace agent).
    assert telemetry_writer._enabled
    assert telemetry_writer._agentless is False
    assert config._dd_api_key in (None, "")


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
        # Read each sensitive key the way settings do; the value must not be reported via telemetry.
        assert get_config("DD_API_KEY") == "SENTINEL_DD_API_KEY"
        assert get_config("DD_APP_KEY") == "SENTINEL_DD_APP_KEY"
        # A non-sensitive control config is still reported, proving reporting is otherwise active.
        get_config("DD_SITE", "datadoghq.com")

    # Flush the queued configurations to the native worker -> test agent.
    telemetry_writer.periodic(force_flush=True)

    configurations = test_agent_session.get_configurations()
    reported_names = {c["name"] for c in configurations}
    assert "DD_API_KEY" not in reported_names, configurations
    assert "DD_APP_KEY" not in reported_names, configurations
    for cfg in configurations:
        assert "SENTINEL_DD_API_KEY" not in str(cfg["value"]), cfg
        assert "SENTINEL_DD_APP_KEY" not in str(cfg["value"]), cfg
    # Sanity check: the non-sensitive control config was reported.
    assert "DD_SITE" in reported_names, configurations


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
    writer = TelemetryWriter()
    assert writer._format_file_path(filename) == result


def test_endpoint_subscription_lifecycle(telemetry_writer):
    """``enable`` subscribes the writer to the endpoint collection, ``disable`` unsubscribes it."""
    from ddtrace.internal.endpoints import endpoint_collection

    assert endpoint_collection.on_endpoint_registered == telemetry_writer._record_endpoint

    telemetry_writer.disable()
    assert endpoint_collection.on_endpoint_registered is None


def test_disable_leaves_a_foreign_endpoint_subscriber_alone(telemetry_writer):
    """Only the writer's own subscription is cleared, so a disable cannot unhook someone else."""
    from ddtrace.internal.endpoints import endpoint_collection

    def other(endpoint):
        pass

    endpoint_collection.on_endpoint_registered = other
    telemetry_writer.disable()

    assert endpoint_collection.on_endpoint_registered is other


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
    # The native worker owns the configuration seq_id and stamps the eagerly-reported
    # ``python_*`` configs first, so absolute seq_ids are offset. Assert the relative order
    # (each source increments the seq_id, in insertion order) instead of absolute values.
    seq_ids = [c["seq_id"] for c in sorted_configs]
    assert seq_ids == sorted(seq_ids) and len(set(seq_ids)) == 6, seq_ids

    assert sorted_configs[0]["value"] == "unamed_python_service"
    assert sorted_configs[0]["origin"] == "default"

    assert sorted_configs[1]["value"] == "otel_service"
    assert sorted_configs[1]["origin"] == "otel_env_var"

    assert sorted_configs[2]["value"] == "dd_service"
    assert sorted_configs[2]["origin"] == "env_var"

    assert sorted_configs[3]["value"] == "monkey"
    assert sorted_configs[3]["origin"] == "code"

    assert sorted_configs[4]["value"] == "baboon"
    assert sorted_configs[4]["origin"] == "remote_config"

    assert sorted_configs[5]["value"] == "baboon"
    assert sorted_configs[5]["origin"] == "fleet_stable_config"


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

    # The native worker serializes every configuration value as a string, so compare against
    # the wire form (see _to_config_str) rather than the DDConfig item's declared type.
    assert reported["DD_TEST_SYNTHETIC_BOOL_SETTING"]["value"] == _to_config_str(True)
    assert reported["DD_TEST_SYNTHETIC_FLOAT_SETTING"]["value"] == _to_config_str(1.5)


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


# err=None: with telemetry debug enabled the native worker logs its actions to stderr, which is
# expected here, so the default "no stderr" check must be relaxed.
@pytest.mark.subprocess(env={"DD_INTERNAL_TELEMETRY_DEBUG_ENABLED": "true"}, err=None)
def test_telemetry_debug_enabled_by_telemetry_env_var():
    """Telemetry debug mode is enabled only by DD_INTERNAL_TELEMETRY_DEBUG_ENABLED, not DD_TRACE_DEBUG."""
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._debug is True


@pytest.mark.subprocess(env={"DD_TRACE_DEBUG": "true"}, err=None)
def test_telemetry_debug_not_enabled_by_tracer_debug():
    """Setting DD_TRACE_DEBUG must not enable telemetry debug mode."""
    from ddtrace.internal.telemetry import telemetry_writer

    assert telemetry_writer._debug is False
