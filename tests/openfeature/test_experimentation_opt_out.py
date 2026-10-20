import copy

from openfeature.evaluation_context import EvaluationContext
from openfeature.exception import ErrorCode
import pytest

from ddtrace.internal.openfeature._config import _set_ffe_config
from ddtrace.internal.openfeature._native import process_ffe_configuration
from ddtrace.openfeature import DataDogProvider
from ddtrace.openfeature import ExperimentationOptOutMode
from tests.openfeature.config_helpers import create_boolean_flag
from tests.openfeature.config_helpers import create_config


ENV = "DD_FEATURE_FLAGS_EXPERIMENTATION_OPT_OUT_MODE"
STAMP = "2099-01-01T00:00:00.123456789+05:30"


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    monkeypatch.setenv("DD_FEATURE_FLAGS_CONFIGURATION_SOURCE", "remote_config")
    monkeypatch.delenv(ENV, raising=False)
    _set_ffe_config(None)
    yield
    _set_ffe_config(None)


@pytest.mark.parametrize(
    "environment,override,expected",
    [
        ("", None, ExperimentationOptOutMode.DISABLED),
        ("disabled", None, ExperimentationOptOutMode.DISABLED),
        ("immediate", None, ExperimentationOptOutMode.IMMEDIATE),
        (" Immediate ", None, ExperimentationOptOutMode.IMMEDIATE),
        ("immediate", ExperimentationOptOutMode.DISABLED, ExperimentationOptOutMode.DISABLED),
        ("disabled", ExperimentationOptOutMode.IMMEDIATE, ExperimentationOptOutMode.IMMEDIATE),
        ("invalid", ExperimentationOptOutMode.DISABLED, ExperimentationOptOutMode.DISABLED),
        ("invalid", ExperimentationOptOutMode.IMMEDIATE, ExperimentationOptOutMode.IMMEDIATE),
    ],
)
def test_configuration_precedence(monkeypatch, environment, override, expected):
    monkeypatch.setenv(ENV, environment)
    provider = DataDogProvider(experimentation_opt_out_mode=override)
    try:
        monkeypatch.setenv(ENV, "changed-after-construction")
        assert provider._experimentation_opt_out_mode == expected
    finally:
        provider.shutdown()


@pytest.mark.parametrize("value", ["future", "unknown"])
def test_invalid_mode(monkeypatch, value):
    monkeypatch.setenv(ENV, value)
    with pytest.raises(ValueError, match="must be disabled or immediate"):
        DataDogProvider()


@pytest.mark.parametrize("mode", [ExperimentationOptOutMode.DISABLED, ExperimentationOptOutMode.IMMEDIATE])
def test_subject_transition_and_fallback(mode, mocker):
    writer = mocker.Mock()
    mocker.patch("ddtrace.internal.openfeature._provider.get_exposure_writer", return_value=writer)
    flag = create_boolean_flag("feature", enabled=True, default_value=True)
    flag["allocations"][0]["doLog"] = True
    ordinary = copy.deepcopy(flag["allocations"][0])
    ordinary["key"] = "ordinary"
    ordinary["doLog"] = False
    ordinary["splits"][0]["variationKey"] = "false"
    flag["allocations"].append(ordinary)
    process_ffe_configuration(create_config(flag))
    provider = DataDogProvider(experimentation_opt_out_mode=mode)
    try:
        eligible = EvaluationContext(targeting_key="athlete")
        opted_out = EvaluationContext(targeting_key="athlete", attributes={"dd_experimentation_opt_out_at": STAMP})
        assert provider.resolve_boolean_details("feature", False, eligible).value is True
        assert writer.enqueue.call_count == 1
        assert provider.resolve_boolean_details("feature", True, opted_out).value is (
            mode == ExperimentationOptOutMode.DISABLED
        )
        assert provider.resolve_boolean_details("feature", False, eligible).value is True
        for value in [None, "", True, 123, "2026-02-30T00:00:00Z"]:
            invalid = EvaluationContext(targeting_key="athlete", attributes={"dd_experimentation_opt_out_at": value})
            result = provider.resolve_boolean_details("feature", False, invalid)
            if mode == ExperimentationOptOutMode.IMMEDIATE:
                assert result.error_code == ErrorCode.INVALID_CONTEXT
                assert result.value is False
            else:
                assert result.error_code is None
                assert result.value is True
        assert writer.enqueue.call_count == 1
    finally:
        provider.shutdown()
