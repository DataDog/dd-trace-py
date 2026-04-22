import pytest


@pytest.mark.subprocess(env={"DD_REMOTE_CONFIGURATION_ENABLED": "false"})
def test_rc_enables_llmobs_and_sets_ml_app():
    """RC payload with llmobs.enabled=true sets config and calls LLMObs.enable()."""
    import mock

    import ddtrace
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._product import apm_tracing_rc

    assert ddtrace.config._llmobs_enabled is False
    with mock.patch.object(LLMObs, "enable") as mock_enable:
        apm_tracing_rc(
            {"llmobs": {"enabled": True, "ml_app_name": "my-llm-app"}},
            ddtrace.config,
        )

    assert ddtrace.config._llmobs_enabled is True
    assert ddtrace.config._llmobs_ml_app == "my-llm-app"
    mock_enable.assert_called_once_with(ml_app="my-llm-app", _auto=True)


@pytest.mark.subprocess(ddtrace_run=True, env={"DD_REMOTE_CONFIGURATION_ENABLED": "false", "DD_LLMOBS_ENABLED": "true"})
def test_rc_disables_llmobs():
    """RC payload with llmobs.enabled=false calls LLMObs.disable() when LLMObs is running."""
    import mock

    import ddtrace
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._product import apm_tracing_rc

    assert LLMObs.enabled, "ddtrace-run with DD_LLMOBS_ENABLED=true should have enabled LLMObs"
    with mock.patch.object(LLMObs, "disable") as mock_disable:
        apm_tracing_rc({"llmobs": {"enabled": False}}, ddtrace.config)

    mock_disable.assert_called_once()
    assert ddtrace.config._llmobs_enabled is False


@pytest.mark.subprocess(
    ddtrace_run=True, env={"DD_REMOTE_CONFIGURATION_ENABLED": "false", "DD_LLMOBS_ENABLED": "false"}
)
def test_rc_missing_llmobs_is_noop():
    """Payloads missing llmobs.enabled do not affect LLMObs state."""
    import mock

    import ddtrace
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._product import apm_tracing_rc

    for payload in ({}, {"llmobs": {}}, {"llmobs": {"enabled": None}}):
        with mock.patch.object(LLMObs, "enable") as mock_enable:
            apm_tracing_rc(payload, ddtrace.config)

        mock_enable.assert_not_called()
        assert ddtrace.config._llmobs_enabled is False
        assert not LLMObs.enabled
