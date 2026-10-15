"""Tests for debug logging of flag evaluations (DD_TRACE_DEBUG self-diagnosis)."""

import logging

from openfeature import api
from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
from openfeature.flag_evaluation import FlagEvaluationDetails
from openfeature.flag_evaluation import FlagType
from openfeature.flag_evaluation import Reason
from openfeature.hook import HookContext
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._debug_logging_hook import DebugLoggingHook
from ddtrace.internal.openfeature._flagevaluation_writer import METADATA_OBSERVE_FULL_EVALUATION_DATA
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config
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
    assert "Flag not found" not in message, message


@pytest.mark.parametrize("consent", [None, False, True], ids=["consent-absent", "protected", "full-data"])
@pytest.mark.parametrize("failed", [False, True], ids=["resolved", "fallback"])
@pytest.mark.parametrize(
    "flag_type,value",
    [
        (FlagType.STRING, "secret-flag-value"),
        (FlagType.OBJECT, {"token": "secret-flag-value"}),
    ],
    ids=["string", "object"],
)
def test_hook_omits_sensitive_data(caplog, consent, failed, flag_type, value):
    metadata = {"custom": "secret-metadata"}
    if consent is not None:
        metadata[METADATA_OBSERVE_FULL_EVALUATION_DATA] = consent
    context = HookContext(
        flag_key="private-flag",
        flag_type=flag_type,
        default_value=value,
        evaluation_context=EvaluationContext(
            targeting_key="secret-targeting-key", attributes={"email": "secret-context-email"}
        ),
    )
    details = FlagEvaluationDetails(
        flag_key="private-flag",
        value=value,
        variant=None if failed else "on",
        reason=Reason.ERROR if failed else Reason.TARGETING_MATCH,
        error_code=ErrorCode.GENERAL if failed else None,
        error_message="Invalid context: secret-error-email" if failed else None,
        flag_metadata=metadata,
    )

    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        DebugLoggingHook().finally_after(context, details, {})

    (message,) = _evaluated_messages(caplog)
    assert "private-flag" in message
    assert ("GENERAL" if failed else "TARGETING_MATCH") in message
    for sensitive in (
        "secret-flag-value",
        "secret-metadata",
        "secret-targeting-key",
        "secret-context-email",
        "secret-error-email",
    ):
        assert sensitive not in caplog.text


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


def test_provider_skips_hook_when_disabled(caplog):
    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        with override_global_config({"feature_flags_enabled": False}):
            provider = DataDogProvider()

    assert provider._debug_logging_hook is None


def test_evaluation_through_client_is_logged(caplog):
    with caplog.at_level(logging.DEBUG, logger=HOOK_LOGGER):
        provider = DataDogProvider()
        process_ffe_configuration(create_config(create_boolean_flag("debug-flag", enabled=True, default_value=True)))
        api.set_provider(provider)
        try:
            client = api.get_client()
            assert client.get_boolean_value("debug-flag", False) is True
            assert client.get_boolean_value("unknown-flag", False) is False
        finally:
            api.clear_providers()

    messages = _evaluated_messages(caplog)
    assert any(m.startswith("Feature Flags: evaluated debug-flag: ") for m in messages), messages
    assert any(m.startswith("Feature Flags: evaluated unknown-flag: ") and "FLAG_NOT_FOUND" in m for m in messages)
