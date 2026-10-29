"""Tests for debug logging of flag evaluations (DD_TRACE_DEBUG self-diagnosis)."""

import logging
from unittest import mock

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagEvaluationOptions
from openfeature.flag_evaluation import FlagType
from openfeature.flag_evaluation import Reason
from openfeature.hook import Hook
from openfeature.hook import HookContext
from openfeature.provider import ProviderStatus
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._debug_logging_hook import DebugLoggingHook
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
from tests.openfeature.conftest import set_openfeature_provider
from tests.utils import override_global_config


HOOK_LOGGER = "ddtrace.internal.openfeature._debug_logging_hook"


@pytest.fixture(autouse=True)
def clear_config():
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


def _hook_context(flag_key):
    return HookContext(
        flag_key=flag_key,
        flag_type=FlagType.BOOLEAN,
        default_value=False,
        evaluation_context=None,
    )


def _evaluated_messages(caplog):
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("Feature Flags: evaluated")]


def test_hook_logs_evaluation_details(caplog):
    details = FlagEvaluationDetails(
        flag_key="my-flag",
        value=True,
        variant="on",
        reason=Reason.TARGETING_MATCH,
        flag_metadata={"allocation_key": "alloc-1"},
    )

    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        DebugLoggingHook().finally_after(_hook_context("my-flag"), details, {})

    (message,) = _evaluated_messages(caplog)
    assert message.startswith("Feature Flags: evaluated my-flag: ")
    for expected in ("variant='on'", "TARGETING_MATCH", "error_code=None"):
        assert expected in message, message


def test_hook_logs_errored_evaluations(caplog):
    details = FlagEvaluationDetails(
        flag_key="missing-flag",
        value=False,
        reason=Reason.ERROR,
        error_code=ErrorCode.FLAG_NOT_FOUND,
        error_message="Flag not found",
    )

    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        DebugLoggingHook().finally_after(_hook_context("missing-flag"), details, {})

    (message,) = _evaluated_messages(caplog)
    assert "FLAG_NOT_FOUND" in message, message
    assert "error_message='Flag not found'" in message, message
    assert "default_value=False" in message, message


@pytest.mark.parametrize(
    "flag_type,value",
    [
        (FlagType.BOOLEAN, True),
        (FlagType.INTEGER, 123),
        (FlagType.FLOAT, 1.25),
        (FlagType.STRING, "raw-flag-value"),
        (FlagType.OBJECT, {"nested": {"items": ["raw-flag-value"] * 3, "big": "x" * 5000}}),
    ],
    ids=["boolean", "integer", "float", "string", "large-object"],
)
def test_hook_logs_raw_values_without_truncation(caplog, flag_type, value):
    context = HookContext(
        flag_key="raw-flag",
        flag_type=flag_type,
        default_value="raw-default",
        evaluation_context=EvaluationContext(targeting_key="user-1", attributes={"email": "user@example.test"}),
    )
    details = FlagEvaluationDetails(
        flag_key="raw-flag",
        value=value,
        variant="on",
        reason=Reason.TARGETING_MATCH,
        flag_metadata={"allocation_key": "alloc-1"},
    )

    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        DebugLoggingHook().finally_after(context, details, {})

    (message,) = _evaluated_messages(caplog)
    assert f"value={value!r}" in message
    assert "default_value='raw-default'" in message
    assert "flag_metadata={'allocation_key': 'alloc-1'}" in message


def test_provider_logs_resolution_with_effective_context(caplog):
    class AddAttribute(Hook):
        def before(self, hook_context, hints):
            return EvaluationContext(attributes={"added-by-hook": True})

    with caplog.at_level(logging.DEBUG, logger="ddtrace.internal.openfeature"):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("debug-flag", enabled=True, default_value=True)))
        set_openfeature_provider(provider)
        try:
            client = api.get_client()
            context = EvaluationContext(targeting_key="user-1", attributes={"plan": "pro"})
            assert client.get_boolean_value("debug-flag", False, context, FlagEvaluationOptions(hooks=[AddAttribute()]))
        finally:
            api.clear_providers()

    (resolved,) = [m for m in caplog.messages if m.startswith("Feature Flags: provider resolved debug-flag: ")]
    assert "targeting_key='user-1'" in resolved, resolved
    assert "'plan': 'pro'" in resolved and "'added-by-hook': True" in resolved, resolved
    assert "default_value=False -> value=True" in resolved, resolved
    assert "'created_at':" in resolved and "'environment':" in resolved
    (evaluated,) = [m for m in _evaluated_messages(caplog) if m.startswith("Feature Flags: evaluated debug-flag: ")]
    assert "value=True" in evaluated, evaluated


def test_provider_resolution_log_does_not_change_result_when_context_read_fails(caplog):
    class FailingContext:
        @property
        def targeting_key(self):
            raise RuntimeError("diagnostic context read failed")

    details = mock.Mock(value=True)
    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        provider = DataDogProvider()
        with mock.patch.object(provider, "_resolve", return_value=details):
            assert provider.resolve_boolean_details("debug-flag", False, FailingContext()) is details


def test_provider_skips_resolution_logging_when_debug_disabled(caplog):
    with caplog.at_level(logging.ERROR, logger=HOOK_LOGGER):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("debug-flag", enabled=True, default_value=True)))
        with mock.patch("ddtrace.internal.openfeature._provider.log_provider_resolution") as log_resolution:
            provider.resolve_boolean_details("debug-flag", False)

    log_resolution.assert_not_called()


def test_provider_registers_hook_when_debug_enabled(caplog):
    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        provider = DataDogProvider()

    assert provider._debug_logging_hook is not None
    assert provider._debug_logging_hook in provider.get_provider_hooks()


def test_provider_skips_hook_when_debug_disabled(caplog):
    # A level above DEBUG (e.g. DD_TRACE_LOG_LEVEL=error) must keep the
    # evaluation path free of debug-logging work.
    with caplog.at_level(logging.ERROR, logger=HOOK_LOGGER):
        provider = DataDogProvider()

    assert provider._debug_logging_hook is None
    assert not any(isinstance(h, DebugLoggingHook) for h in provider.get_provider_hooks())


def _raw_data_warnings(caplog):
    return [
        r
        for r in caplog.records
        if r.levelno == logging.WARNING and "feature flag logs include raw evaluation data" in r.getMessage()
    ]


def test_provider_warns_once_about_raw_data_when_debug_enabled(caplog):
    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("debug-flag", enabled=True, default_value=True)))
        set_openfeature_provider(provider)
        try:
            client = api.get_client()
            assert client.get_boolean_value("debug-flag", False) is True
            assert client.get_boolean_value("debug-flag", False) is True
        finally:
            api.clear_providers()

    (warning,) = _raw_data_warnings(caplog)
    assert warning.getMessage() == (
        "Feature Flags: debug logging is enabled, so feature flag logs include raw evaluation data (targeting keys, "
        "evaluation context attributes, flag values, and default values) that may contain personal or sensitive "
        "information. Disable debug logging (DD_TRACE_DEBUG, or a DEBUG DD_TRACE_LOG_LEVEL) when you no longer need it."
    )


def test_provider_does_not_warn_about_raw_data_when_debug_disabled(caplog):
    with caplog.at_level(logging.WARNING, logger=HOOK_LOGGER):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("debug-flag", enabled=True, default_value=True)))
        provider.resolve_boolean_details("debug-flag", False)

    assert not _raw_data_warnings(caplog)


def test_provider_does_not_warn_about_raw_data_when_disabled(caplog):
    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        with override_global_config({"feature_flags_enabled": False}):
            DataDogProvider()

    assert not _raw_data_warnings(caplog)


def test_debug_enabled_after_creation_logs_no_raw_data(caplog):
    # The creation-time warning is enough only because a provider created without
    # debug never starts logging raw evaluation data later.
    with caplog.at_level(logging.WARNING, logger=HOOK_LOGGER):
        provider = DataDogProvider()
    process_ffe_configuration(create_config(create_boolean_flag("debug-flag", enabled=True, default_value=True)))

    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        context = EvaluationContext(targeting_key="user-1", attributes={"plan": "pro"})
        assert provider.resolve_boolean_details("debug-flag", False, context).value is True

    assert not _raw_data_warnings(caplog)
    assert not [m for m in caplog.messages if m.startswith("Feature Flags: provider resolved")]


def test_provider_skips_hook_when_disabled(caplog):
    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        with override_global_config({"feature_flags_enabled": False}):
            provider = DataDogProvider()

    assert provider._debug_logging_hook is None


def test_evaluation_through_client_is_logged(caplog):
    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("debug-flag", enabled=True, default_value=True)))
        set_openfeature_provider(provider)
        try:
            client = api.get_client()
            assert client.get_boolean_value("debug-flag", False) is True
            assert client.get_boolean_value("unknown-flag", False) is False
        finally:
            api.clear_providers()

    messages = _evaluated_messages(caplog)
    assert any(m.startswith("Feature Flags: evaluated debug-flag: ") for m in messages), messages
    assert any(m.startswith("Feature Flags: evaluated unknown-flag: ") and "FLAG_NOT_FOUND" in m for m in messages)


@pytest.mark.parametrize("not_ready", [False, True])
def test_sdk_fallback_logs_input_context_without_provider_resolution(caplog, not_ready):
    class FailingHook(Hook):
        def before(self, hook_context, hints):
            raise RuntimeError("application hook failed")

    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("flag", default_value=True)))
        set_openfeature_provider(provider)
        try:
            client = api.get_client()
            with mock.patch.object(
                client,
                "get_provider_status",
                return_value=(ProviderStatus.NOT_READY if not_ready else ProviderStatus.READY),
            ):
                result = client.get_boolean_details(
                    "flag",
                    False,
                    EvaluationContext(targeting_key="user", attributes={"plan": "pro"}),
                    FlagEvaluationOptions(hooks=[FailingHook()]),
                )
        finally:
            api.clear_providers()

    assert result.value is False
    assert not any(m.startswith("Feature Flags: provider resolved") for m in caplog.messages)
    (message,) = _evaluated_messages(caplog)
    assert "input_targeting_key='user'" in message
    assert "input_attributes=" in message and "'plan': 'pro'" in message
    assert "PROVIDER_NOT_READY" in message if not_ready else "application hook failed" in message


def test_observation_does_not_claim_to_follow_application_finally_hooks(caplog):
    class ChangeFinally(Hook):
        def finally_after(self, hook_context, details, hints):
            details.value = False

    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("flag", default_value=True)))
        set_openfeature_provider(provider)
        try:
            assert (
                api.get_client().get_boolean_value(
                    "flag", False, flag_evaluation_options=FlagEvaluationOptions(hooks=[ChangeFinally()])
                )
                is False
            )
        finally:
            api.clear_providers()
    (message,) = _evaluated_messages(caplog)
    assert "value=True" in message
    assert "before application finally hooks" in message


def test_debug_disabled_after_construction_does_not_inspect_inputs(caplog):
    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        provider = DataDogProvider()
    with caplog.at_level(logging.WARNING, logger=HOOK_LOGGER):
        context = mock.Mock()
        with mock.patch.object(provider, "_resolve", return_value=mock.Mock(value=True)):
            provider.resolve_boolean_details("flag", False, context)
        context.assert_not_called()
        assert "targeting_key" not in context._mock_children
