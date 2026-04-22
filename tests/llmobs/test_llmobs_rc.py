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

    for payload in ({}, {"llmobs": None}, {"llmobs": {}}, {"llmobs": {"enabled": None}}):
        with mock.patch.object(LLMObs, "enable") as mock_enable:
            apm_tracing_rc(payload, ddtrace.config)

        mock_enable.assert_not_called()
        assert ddtrace.config._llmobs_enabled is False
        assert not LLMObs.enabled


@pytest.mark.subprocess(ddtrace_run=True, env={"DD_REMOTE_CONFIGURATION_ENABLED": "false", "DD_LLMOBS_ENABLED": "true"})
def test_rc_missing_llmobs_does_not_disable_enabled_llmobs():
    """A payload without an llmobs.enabled directive must not disable already-enabled LLMObs."""
    import mock

    import ddtrace
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._product import apm_tracing_rc

    assert LLMObs.enabled
    for payload in ({}, {"llmobs": {}}, {"llmobs": {"ml_app_name": "some-app"}}):
        with mock.patch.object(LLMObs, "disable") as mock_disable:
            apm_tracing_rc(payload, ddtrace.config)

        mock_disable.assert_not_called()
        assert LLMObs.enabled


@pytest.mark.subprocess(env={"DD_REMOTE_CONFIGURATION_ENABLED": "false"})
def test_rc_directive_removal_clears_rc_override():
    """When an llmobs directive is present on one poll and absent on the next
    (e.g. the upstream RC config was removed), the handler must clear the stale
    _rc_value so _ConfigItem.value() falls back through env/code/default.
    """
    import mock

    import ddtrace
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._product import apm_tracing_rc

    enabled_config = ddtrace.config._config["_llmobs_enabled"]
    ml_app_config = ddtrace.config._config["_llmobs_ml_app"]

    with mock.patch.object(LLMObs, "enable"), mock.patch.object(LLMObs, "disable"):
        apm_tracing_rc({"llmobs": {"enabled": True, "ml_app_name": "rc-app"}}, ddtrace.config)
        assert enabled_config._rc_value is True
        assert ml_app_config._rc_value == "rc-app"

        apm_tracing_rc({}, ddtrace.config)
        assert enabled_config._rc_value is None
        assert ml_app_config._rc_value is None


@pytest.mark.subprocess(env={"DD_REMOTE_CONFIGURATION_ENABLED": "false"})
def test_rc_missing_llmobs_does_not_disable_programmatically_enabled_llmobs():
    """Regression: payloads without an llmobs directive must not disable LLMObs
    when it was enabled programmatically via LLMObs.enable() (no DD_LLMOBS_ENABLED
    env var). The pre-fix handler branched on _ConfigItem.value(), which fell
    through to the default False and fired LLMObs.disable() on every RC poll.
    The fix relies on LLMObs.enable() writing _code_value=True so value() reflects
    the effective state.
    """
    import mock

    import ddtrace
    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._product import apm_tracing_rc

    # Simulate the post-LLMObs.enable() state: class attribute flipped True AND
    # ddtrace.config._llmobs_enabled written to "code" source (what enable() does
    # via Config.__setattr__).
    LLMObs.enabled = True
    ddtrace.config._llmobs_enabled = True
    enabled_item = ddtrace.config._config["_llmobs_enabled"]
    assert enabled_item.value() is True, "precondition: post-enable invariant holds"

    for payload in ({}, {"llmobs": {}}, {"llmobs": {"ml_app_name": "some-app"}}):
        with mock.patch.object(LLMObs, "disable") as mock_disable:
            apm_tracing_rc(payload, ddtrace.config)

        mock_disable.assert_not_called()
        assert LLMObs.enabled
