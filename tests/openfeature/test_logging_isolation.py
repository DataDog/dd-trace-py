"""A raising application log handler must not change Feature Flagging behavior."""

import json
import logging
import threading

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagType
from openfeature.hook import HookContext
from openfeature.provider import ProviderStatus
import pytest

from ddtrace.internal.logger import get_safe_logger as get_logger
import ddtrace.internal.openfeature._agentless_source as source_mod
from ddtrace.internal.openfeature._agentless_source import AgentlessConfigurationSource
from ddtrace.internal.openfeature._config import _get_ffe_config
from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._debug_logging_hook import DebugLoggingHook
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.internal.openfeature._remoteconfiguration import FeatureFlagCallback
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.settings.openfeature import OpenFeatureConfig
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config


FFE_LOGGER = "ddtrace.internal.openfeature"


class _RaisingHandler(logging.Handler):
    """Fails like an application handler that does not guard its own I/O."""

    def handle(self, record):
        raise RuntimeError("log handler failed")


class _FormattingHandler(logging.Handler):
    """Formats without guarding, like a handler that does not call handleError."""

    def handle(self, record):
        self.format(record)


class _RecordingHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.records = []

    def emit(self, record):
        self.records.append(record)


@pytest.fixture(autouse=True)
def clear_config():
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.fixture
def raising_handler():
    """Attach a raising handler to every Feature Flagging logger, with debug output on."""
    logger = logging.getLogger(FFE_LOGGER)
    handler = _RaisingHandler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    yield
    logger.removeHandler(handler)
    logger.setLevel(previous_level)


def test_isolated_logger_drops_failures_and_keeps_the_call_site():
    logger = get_logger(FFE_LOGGER + ".test_isolation")
    recording = _RecordingHandler()
    raising = _RaisingHandler()
    logger.addHandler(recording)
    logger.addHandler(raising)
    try:
        logger.warning("Feature Flags: %s", "message")
    finally:
        logger.removeHandler(recording)
        logger.removeHandler(raising)

    (record,) = recording.records
    # The rate limiter keys records by call site, so isolation must not move it.
    assert record.pathname == __file__
    assert record.funcName == "test_isolated_logger_drops_failures_and_keeps_the_call_site"


def test_initialization_and_late_readiness_survive_a_raising_handler(raising_handler):
    provider = DataDogProvider(initialization_timeout=5.0)
    timer = threading.Timer(
        0.05, process_ffe_configuration, args=(create_config(create_boolean_flag("late-flag", enabled=True)),)
    )
    timer.start()
    try:
        provider.initialize(EvaluationContext())
        assert provider._status == ProviderStatus.READY
    finally:
        timer.join()
        provider.shutdown()


def test_evaluation_and_shutdown_survive_a_raising_handler(raising_handler):
    provider = DataDogProvider()
    assert provider._debug_logging_hook is not None
    process_ffe_configuration(create_config(create_boolean_flag("debug-flag", enabled=True, default_value=True)))
    api.set_provider(provider)
    try:
        client = api.get_client()
        details = client.get_boolean_details("debug-flag", False, EvaluationContext(targeting_key="user-1"))
        assert details.value is True
        assert details.error_code is None
        assert client.get_boolean_value("unknown-flag", False) is False
    finally:
        api.clear_providers()

    assert provider._status == ProviderStatus.NOT_READY


def test_remote_config_application_survives_a_raising_handler(raising_handler):
    callback = FeatureFlagCallback()
    metadata = ConfigMetadata(id="config", product_name="FFE_FLAGS", sha256_hash=None, length=None, tuf_version=1)
    path = "datadog/2/FFE_FLAGS/config/config"

    callback([Payload(metadata=metadata, path=path, content=create_config(create_boolean_flag("a")))])
    assert _get_ffe_config() is not None

    callback([Payload(metadata=metadata, path=path, content=None)])
    assert _get_ffe_config() is None


def test_agentless_application_survives_a_raising_handler(raising_handler, monkeypatch):
    body = json.dumps(
        {
            "data": {
                "type": "universal-flag-configuration",
                "attributes": {
                    "format": "SERVER",
                    "createdAt": "2024-01-01T00:00:00Z",
                    "environment": {"name": "production"},
                    "flags": {},
                },
            }
        }
    ).encode("utf-8")

    class Response:
        status = 200

        def read(self):
            return body

        def getheader(self, name, default=None):
            return '"v1"' if name == "ETag" else default

    class Connection:
        sock = None

        def request(self, *args):
            pass

        def getresponse(self):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr(source_mod, "get_connection", lambda url, timeout=None: Connection())
    applied = []
    src = AgentlessConfigurationSource(
        endpoint="https://flags.example.test/config", apply_configuration=applied.append, env="prod"
    )
    src.periodic()

    assert len(applied) == 1
    assert src._etag == '"v1"'


def test_evaluation_value_that_fails_to_format_does_not_escape_the_hook():
    class Unprintable:
        def __repr__(self):
            raise ValueError("cannot format")

    logger = logging.getLogger(FFE_LOGGER)
    handler = _FormattingHandler()
    previous_level = logger.level
    logger.addHandler(handler)
    logger.setLevel(logging.DEBUG)
    try:
        DebugLoggingHook().finally_after(
            HookContext(flag_key="f", flag_type=FlagType.OBJECT, default_value=None, evaluation_context=None),
            FlagEvaluationDetails(flag_key="f", value=Unprintable()),
            {},
        )
    finally:
        logger.removeHandler(handler)
        logger.setLevel(previous_level)


def test_record_factory_failure_does_not_interrupt_provider_lifecycle(monkeypatch, caplog):
    def fail_factory(*args, **kwargs):
        raise RuntimeError("record factory failed")

    with caplog.at_level(logging.DEBUG, logger=FFE_LOGGER):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("flag", default_value=True)))
        monkeypatch.setattr(logging, "_logRecordFactory", fail_factory)
        provider.initialize(EvaluationContext())
        assert provider._status == ProviderStatus.READY
        assert provider.resolve_boolean_details("flag", False).value is True
        provider.shutdown()
        assert provider._status == ProviderStatus.NOT_READY


def test_invalid_setting_survives_a_raising_handler(monkeypatch):
    logger = logging.getLogger("ddtrace.internal.settings.openfeature")
    handler = _RaisingHandler()
    logger.addHandler(handler)
    monkeypatch.setenv("DD_FEATURE_FLAGS_CONFIGURATION_SOURCE_AGENTLESS_POLL_INTERVAL_SECONDS", "invalid")
    try:
        assert OpenFeatureConfig().configuration_source_agentless_poll_interval_seconds == 30
    finally:
        logger.removeHandler(handler)
