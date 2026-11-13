import pytest


@pytest.mark.subprocess(env={"DD_REMOTE_CONFIGURATION_ENABLED": "false"})
def test_rc_enables_llmobs_and_sets_ml_app():
    """RC payload with llmobs.enabled=true sets config and calls LLMObs.enable()."""
    from unittest import mock

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
    from unittest import mock

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
    from unittest import mock

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
    from unittest import mock

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
    from unittest import mock

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
    from unittest import mock

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


@pytest.mark.subprocess(
    env={"DD_LLMOBS_ENABLED": "true", "DD_LLMOBS_ML_APP": "assistant_api", "DD_REMOTE_CONFIGURATION_ENABLED": "true"}
)
def test_apm_tracing_payload_during_llmobs_product_start_does_not_self_parent_spans():
    """Regression: a service enables LLMObs with DD_LLMOBS_ENABLED and gets an APM_TRACING
    record without an llmobs directive. If the poller delivers it while product start is still
    inside LLMObs.enable(), the RC handler used to enable LLMObs a second time on the poller
    thread. Both instances hooked span start, and every span became its own parent.
    """
    import threading
    import time
    from unittest import mock

    from ddtrace.internal.native import RemoteConfigProduct
    from ddtrace.internal.remoteconfig import ConfigMetadata
    from ddtrace.internal.remoteconfig import Payload
    from ddtrace.llmobs import _llmobs

    def deliver_apm_tracing_record():
        from ddtrace.internal.remoteconfig.worker import remoteconfig_poller

        callback = remoteconfig_poller._client._product_callbacks[RemoteConfigProduct.ApmTracing]
        callback(
            [
                Payload(
                    ConfigMetadata(
                        id="sampling", product_name="APM_TRACING", sha256_hash=None, length=None, tuf_version=1
                    ),
                    "datadog/2/APM_TRACING/sampling/config",
                    {"lib_config": {"tracing_sampling_rate": 1.0}, "service_target": {"service": "*", "env": "*"}},
                )
            ]
        )

    poller = []
    resolve_git_metadata = _llmobs.resolve_llmobs_git_metadata

    def resolve_git_metadata_while_poller_delivers():
        if not poller:
            # Product start is inside LLMObs.enable() on the main thread. Deliver the record on
            # another thread, like the poller, and keep enable() going while it is handled.
            poller.append(threading.Thread(target=deliver_apm_tracing_record))
            poller[0].start()
            time.sleep(0.5)
        return resolve_git_metadata()

    with mock.patch.object(_llmobs, "resolve_llmobs_git_metadata", resolve_git_metadata_while_poller_delivers):
        import ddtrace.auto  # noqa: F401
    assert poller, "product start did not enable LLMObs"
    poller[0].join()

    from ddtrace.llmobs import LLMObs
    from ddtrace.llmobs._constants import ROOT_PARENT_ID
    from ddtrace.llmobs._utils import get_llmobs_parent_id

    with LLMObs.agent("assistant") as agent:
        with LLMObs.workflow("get_answer_from_model_step") as workflow:
            with LLMObs.llm(model_name="claude", name="anthropic.request") as llm:
                pass

    assert get_llmobs_parent_id(agent) == ROOT_PARENT_ID
    assert get_llmobs_parent_id(workflow) == str(agent.span_id)
    assert get_llmobs_parent_id(llm) == str(workflow.span_id)
