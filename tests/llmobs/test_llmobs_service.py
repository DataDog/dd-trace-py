import json
import os
import re
import threading
import time
import urllib.parse

import mock
import pytest

import ddtrace
from ddtrace.ext import SpanTypes
from ddtrace.internal.telemetry.constants import TELEMETRY_NAMESPACE
from ddtrace.internal.utils.formats import format_trace_id
from ddtrace.llmobs import LLMObs as llmobs_service
from ddtrace.llmobs._constants import EXPERIMENT_ID_KEY
from ddtrace.llmobs._constants import LLMOBS_STRUCT
from ddtrace.llmobs._constants import ML_APP
from ddtrace.llmobs._constants import PROMPT_TRACKING_INSTRUMENTATION_METHOD
from ddtrace.llmobs._constants import PROPAGATED_LLMOBS_TRACE_ID_KEY
from ddtrace.llmobs._constants import PROPAGATED_ML_APP_KEY
from ddtrace.llmobs._constants import PROPAGATED_PARENT_ID_KEY
from ddtrace.llmobs._constants import SESSION_ID
from ddtrace.llmobs._constants import SPAN_START_WHILE_DISABLED_WARNING
from ddtrace.llmobs._constants import SUPPORTED_LLMOBS_INTEGRATIONS
from ddtrace.llmobs._constants import UNKNOWN_MODEL_NAME
from ddtrace.llmobs._constants import UNKNOWN_MODEL_PROVIDER
from ddtrace.llmobs._telemetry import LLMObsTelemetryMetrics
from ddtrace.llmobs._utils import _annotate_llmobs_span_data
from ddtrace.llmobs._utils import _get_llmobs_data_metastruct
from ddtrace.llmobs._utils import get_llmobs_cost_tags
from ddtrace.llmobs._utils import get_llmobs_input_documents
from ddtrace.llmobs._utils import get_llmobs_input_messages
from ddtrace.llmobs._utils import get_llmobs_input_prompt
from ddtrace.llmobs._utils import get_llmobs_input_value
from ddtrace.llmobs._utils import get_llmobs_metadata
from ddtrace.llmobs._utils import get_llmobs_metrics
from ddtrace.llmobs._utils import get_llmobs_ml_app
from ddtrace.llmobs._utils import get_llmobs_model_name
from ddtrace.llmobs._utils import get_llmobs_model_provider
from ddtrace.llmobs._utils import get_llmobs_output_documents
from ddtrace.llmobs._utils import get_llmobs_output_messages
from ddtrace.llmobs._utils import get_llmobs_output_value
from ddtrace.llmobs._utils import get_llmobs_parent_id
from ddtrace.llmobs._utils import get_llmobs_session_id
from ddtrace.llmobs._utils import get_llmobs_span_kind
from ddtrace.llmobs._utils import get_llmobs_span_links
from ddtrace.llmobs._utils import get_llmobs_span_name
from ddtrace.llmobs._utils import get_llmobs_tags
from ddtrace.llmobs._utils import get_llmobs_trace_id
from ddtrace.llmobs.types import Prompt
from ddtrace.trace import Context
from tests.llmobs._utils import _expected_llmobs_eval_metric_event
from tests.llmobs._utils import _expected_llmobs_feedback_event
from tests.llmobs._utils import assert_llmobs_span_data
from tests.utils import override_env
from tests.utils import override_global_config


RAGAS_AVAILABLE = os.getenv("RAGAS_AVAILABLE", False)


def run_llmobs_trace_filter(tracer, test_spans):
    with tracer.trace("span1", span_type=SpanTypes.LLM) as span:
        _annotate_llmobs_span_data(span, kind="llm")
    return test_spans.pop()


def test_service_enable_proxy(tracer, test_spans):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable(_tracer=tracer, agentless_enabled=False)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == tracer
        assert llmobs_instance._llmobs_span_writer._agentless is False
        assert run_llmobs_trace_filter(tracer, test_spans) is not None
        llmobs_service.disable()


def test_service_enable_agent_service_precedence(tracer):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<config-ml-app>")):
        llmobs_service.enable(
            _tracer=tracer,
            agentless_enabled=False,
            ml_app="<legacy-ml-app>",
            agent_service="<agent-service>",
        )
        assert ddtrace.config._llmobs_ml_app == "<agent-service>"
        with llmobs_service.workflow() as span:
            pass
        assert get_llmobs_ml_app(span) == "<agent-service>"
        llmobs_service.disable()


def test_service_enable_agent_service_precedence_over_service(tracer):
    """agent_service takes precedence over both ml_app and service when enabling."""
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>")):
        llmobs_service.enable(
            _tracer=tracer,
            agentless_enabled=False,
            service="<service>",
            ml_app="<legacy-ml-app>",
            agent_service="<agent-service>",
        )
        with llmobs_service.workflow() as span:
            pass
        assert get_llmobs_ml_app(span) == "<agent-service>"
        llmobs_service.disable()


def test_service_enable_service_used_as_ml_app_fallback(tracer):
    """When neither agent_service nor ml_app is set, service is used as the ml app."""
    # Guard against leaked enabled=True from a prior failed test
    llmobs_service.disable()
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app=None)):
        llmobs_service.enable(_tracer=tracer, agentless_enabled=False, service="<service>")
        try:
            with llmobs_service.workflow() as span:
                pass
            assert get_llmobs_ml_app(span) == "<service>"
        finally:
            llmobs_service.disable()


@pytest.mark.subprocess(
    env={"DD_API_KEY": "<not-a-real-key>", "DD_LLMOBS_ML_APP": "<ml-app-name>"},
)
def test_enable_agentless():
    import ddtrace
    from ddtrace.llmobs import LLMObs as llmobs_service

    llmobs_service.enable(agentless_enabled=True)
    assert llmobs_service.enabled
    assert llmobs_service._instance._llmobs_span_writer._agentless is True
    assert ddtrace.tracer._span_aggregator.writer.agentless is True
    llmobs_service.disable()


def test_enable_agent_proxy_when_agent_is_available(tracer, agent):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable(_tracer=tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance._llmobs_span_writer._agentless is False

        llmobs_service.disable()


def test_enable_agentless_when_agent_info_is_not_available(tracer, no_agent_info):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable(_tracer=tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance._llmobs_span_writer._agentless is True

        llmobs_service.disable()


def test_enable_agentless_when_agent_is_not_available(tracer, no_agent):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable(_tracer=tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance._llmobs_span_writer._agentless is True

        llmobs_service.disable()


def test_enable_agentless_when_agent_does_not_have_proxy(tracer, agent_missing_proxy):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable(_tracer=tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance._llmobs_span_writer._agentless is True

        llmobs_service.disable()


@pytest.mark.subprocess(env={"DD_API_KEY": "<not-a-real-key>"})
def test_configure_agentless_writer_swaps_writer():
    import ddtrace
    from ddtrace.llmobs import LLMObs as llmobs_service

    llmobs_service.enable(agentless_enabled=False)
    assert ddtrace.tracer._span_aggregator.writer.agentless is False
    llmobs_service.disable()
    assert ddtrace.tracer._span_aggregator.writer.agentless is False
    llmobs_service.enable(agentless_enabled=True)
    assert ddtrace.tracer._span_aggregator.writer.agentless is True
    llmobs_service.disable()
    assert ddtrace.tracer._span_aggregator.writer.agentless is False


@pytest.mark.subprocess(env={"DD_API_KEY": "", "DD_LLMOBS_AGENTLESS_ENABLED": "1"})
def test_enable_without_api_key_does_not_swap_apm_writer():
    import ddtrace
    from ddtrace.llmobs import LLMObs as llmobs_service

    try:
        llmobs_service.enable()
    except ValueError:
        pass
    assert ddtrace.tracer._span_aggregator.writer.agentless is False


@pytest.mark.subprocess(
    env={
        "DD_API_KEY": "<not-a-real-key>",
        "DD_LLMOBS_AGENTLESS_ENABLED": "1",
        "DD_LLMOBS_ML_APP": "test-ml-app",
    },
    err=None,
)
def test_export_mode_apm_agentless_when_agentless_enabled():
    from ddtrace.llmobs import LLMObs as llmobs_service
    from ddtrace.llmobs._constants import LLMObsExportMode

    llmobs_service.enable()
    assert llmobs_service._instance._export_mode == LLMObsExportMode.APM_AGENTLESS


def test_annotate_tag_values_are_stringified(llmobs):
    """Non-string tag values (bool/int/float/None) are coerced to strings, since the LLMObs
    intakes decode tags as a string->string map.
    """
    with llmobs.workflow("w") as span:
        llmobs.annotate(
            span=span,
            tags={"is_streaming": True, "retries": 3, "ratio": 0.5, "none_tag": None, "str_tag": "ok"},
        )
    tags = get_llmobs_tags(span)
    assert all(isinstance(v, str) for v in tags.values()), tags
    assert {
        "is_streaming": "True",
        "retries": "3",
        "ratio": "0.5",
        "none_tag": "None",
        "str_tag": "ok",
    }.items() <= tags.items()


@pytest.mark.subprocess(
    env={
        "DD_LLMOBS_AGENTLESS_ENABLED": "0",
        "DD_LLMOBS_ML_APP": "test-ml-app",
    },
    err=None,
)
def test_export_mode_apm_agent_when_agentless_disabled():
    """When agentless is explicitly disabled and APM tracing is on, data rides the APM trace via agent."""
    from ddtrace.llmobs import LLMObs as llmobs_service
    from ddtrace.llmobs._constants import LLMObsExportMode

    llmobs_service.enable(agentless_enabled=False)
    assert llmobs_service._instance._export_mode == LLMObsExportMode.APM_AGENT


@pytest.mark.subprocess(
    env={
        "DD_APM_TRACING_ENABLED": "false",
        "DD_LLMOBS_AGENTLESS_ENABLED": "1",
        "DD_LLMOBS_ML_APP": "test-ml-app",
        "DD_API_KEY": "<not-a-real-key>",
    },
    err=None,
)
def test_export_mode_llmobs_agentless_when_apm_tracing_disabled_and_agentless_enabled():
    """APM trace dropped + agentless: events ship via the writer directly to intake."""
    from ddtrace.llmobs import LLMObs as llmobs_service
    from ddtrace.llmobs._constants import LLMObsExportMode

    llmobs_service.enable()
    assert llmobs_service._instance._export_mode == LLMObsExportMode.LLMOBS_AGENTLESS


@pytest.mark.subprocess(
    env={
        "DD_APM_TRACING_ENABLED": "false",
        "DD_LLMOBS_AGENTLESS_ENABLED": "0",
        "DD_LLMOBS_ML_APP": "test-ml-app",
    },
    err=None,
)
def test_export_mode_llmobs_agent_proxy_when_apm_tracing_disabled_and_agentless_disabled():
    """APM trace dropped + agent proxy: events ship via the writer through the Agent EVP proxy."""
    from ddtrace.llmobs import LLMObs as llmobs_service
    from ddtrace.llmobs._constants import LLMObsExportMode

    llmobs_service.enable(agentless_enabled=False)
    assert llmobs_service._instance._export_mode == LLMObsExportMode.LLMOBS_AGENT_PROXY


@pytest.mark.subprocess(
    env={
        "DD_LLMOBS_OVERRIDE_ORIGIN": "http://localhost:1234",
        "DD_LLMOBS_AGENTLESS_ENABLED": "1",
        "DD_LLMOBS_ML_APP": "test-ml-app",
        "DD_API_KEY": "<not-a-real-key>",
    },
    err=None,
)
def test_export_mode_llmobs_agentless_when_override_origin_set_and_agentless_enabled():
    """An override origin must not be silently ignored by letting events ride the APM trace."""
    from ddtrace.llmobs import LLMObs as llmobs_service
    from ddtrace.llmobs._constants import LLMObsExportMode

    llmobs_service.enable()
    assert llmobs_service._instance._export_mode == LLMObsExportMode.LLMOBS_AGENTLESS


@pytest.mark.subprocess(
    env={
        "DD_LLMOBS_OVERRIDE_ORIGIN": "http://localhost:1234",
        "DD_LLMOBS_AGENTLESS_ENABLED": "0",
        "DD_LLMOBS_ML_APP": "test-ml-app",
    },
    err=None,
)
def test_export_mode_llmobs_agent_proxy_when_override_origin_set_and_agentless_disabled():
    """An override origin must not be silently ignored by letting events ride the APM trace."""
    from ddtrace.llmobs import LLMObs as llmobs_service
    from ddtrace.llmobs._constants import LLMObsExportMode

    llmobs_service.enable(agentless_enabled=False)
    assert llmobs_service._instance._export_mode == LLMObsExportMode.LLMOBS_AGENT_PROXY


def test_service_disable(tracer):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable(_tracer=tracer)
        llmobs_service.disable()
        assert llmobs_service.enabled is False
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "stopped"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "stopped"
        assert llmobs_service._instance._evaluator_runner.status.value == "stopped"


@pytest.mark.subprocess(
    env={
        "DD_API_KEY": "<not-a-real-key>",
        "DD_LLMOBS_AGENTLESS_ENABLED": "1",
        "DD_LLMOBS_ML_APP": "test-ml-app",
    }
)
def test_disable_reverts_agentless_writer_when_llmobs_enabled_it():
    """disable() reverts the APM writer when enable() was the one that switched it to agentless."""
    import ddtrace
    from ddtrace.llmobs import LLMObs as llmobs_service

    assert ddtrace.tracer._span_aggregator.writer.agentless is False
    llmobs_service.enable()
    assert llmobs_service._instance._apm_writer_switched_to_agentless is True
    assert ddtrace.tracer._span_aggregator.writer.agentless is True
    llmobs_service.disable()
    assert ddtrace.tracer._span_aggregator.writer.agentless is False


@pytest.mark.subprocess(
    env={
        "DD_API_KEY": "<not-a-real-key>",
        "DD_LLMOBS_AGENTLESS_ENABLED": "1",
        "_DD_APM_TRACING_AGENTLESS_ENABLED": "1",
        "DD_LLMOBS_ML_APP": "test-ml-app",
    }
)
def test_disable_does_not_revert_agentless_writer_when_already_agentless():
    """disable() leaves the APM writer alone when the writer was already agentless before enable()."""
    import ddtrace
    from ddtrace.llmobs import LLMObs as llmobs_service

    assert ddtrace.tracer._span_aggregator.writer.agentless is True
    llmobs_service.enable()
    assert llmobs_service._instance._apm_writer_switched_to_agentless is False
    llmobs_service.disable()
    assert ddtrace.tracer._span_aggregator.writer.agentless is True


def test_enable_disable_keeps_global_config_llmobs_enabled_in_sync(tracer):
    """LLMObs.enable()/disable() must mirror their effect into ddtrace.config._llmobs_enabled
    so the _ConfigItem reflects effective state. Consumers like the APM_TRACING RC handler
    read this value to reconcile LLMObs state against RC payloads.
    """
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        assert ddtrace.config._llmobs_enabled is False
        llmobs_service.enable(_tracer=tracer)
        assert ddtrace.config._llmobs_enabled is True
        llmobs_service.disable()
        assert ddtrace.config._llmobs_enabled is False


def test_service_enable_no_api_key(tracer):
    with override_global_config(dict(_dd_api_key="", _llmobs_ml_app="<ml-app-name>")):
        # enable() raises before replacing _instance, so reset to a fresh real instance:
        # a prior xdist-worker test may have left a mocked eval writer (status != "stopped").
        llmobs_service._instance = llmobs_service()
        with pytest.raises(ValueError):
            llmobs_service.enable(_tracer=tracer, agentless_enabled=True)
        assert llmobs_service.enabled is False
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "stopped"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "stopped"
        assert llmobs_service._instance._evaluator_runner.status.value == "stopped"


def test_service_enable_already_enabled(tracer, mock_llmobs_logs):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable(_tracer=tracer)
        llmobs_service.enable(_tracer=tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_instance.tracer == tracer
        llmobs_service.disable()
        mock_llmobs_logs.debug.assert_has_calls([mock.call("%s already enabled", "LLMObs")])


@mock.patch("ddtrace.llmobs._llmobs.patch")
def test_service_enable_patches_llmobs_integrations(llmobs_patch):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable()
        llmobs_patch.assert_called_once()
        kwargs = llmobs_patch.call_args[1]
        for module in SUPPORTED_LLMOBS_INTEGRATIONS.values():
            assert kwargs[module] is True if module != "botocore" else ["bedrock-runtime"]
        llmobs_service.disable()


@mock.patch("ddtrace.llmobs._llmobs.patch")
def test_service_enable_does_not_override_global_patch_modules(llmobs_patch, monkeypatch):
    monkeypatch.setenv("DD_PATCH_MODULES", "openai:false")
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable()
        llmobs_patch.assert_called_once()
        kwargs = llmobs_patch.call_args[1]
        for module in SUPPORTED_LLMOBS_INTEGRATIONS.values():
            if module == "openai":
                assert kwargs[module] is False
                continue
            assert kwargs[module] is True if module != "botocore" else ["bedrock-runtime"]
        llmobs_service.disable()


@mock.patch("ddtrace.llmobs._llmobs.patch")
def test_service_enable_does_not_override_integration_enabled_env_vars(llmobs_patch, monkeypatch):
    monkeypatch.setenv("DD_TRACE_OPENAI_ENABLED", "false")
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable()
        llmobs_patch.assert_called_once()
        kwargs = llmobs_patch.call_args[1]
        for module in SUPPORTED_LLMOBS_INTEGRATIONS.values():
            if module == "openai":
                assert kwargs[module] is False
                continue
            assert kwargs[module] is True if module != "botocore" else ["bedrock-runtime"]
        llmobs_service.disable()


@mock.patch("ddtrace.llmobs._llmobs.patch")
def test_service_enable_does_not_override_global_patch_config(llmobs_patch, monkeypatch):
    """Test that _patch_integrations() ensures `DD_PATCH_MODULES` overrides `DD_TRACE_<MODULE>_ENABLED`."""
    monkeypatch.setenv("DD_TRACE_OPENAI_ENABLED", "true")
    monkeypatch.setenv("DD_TRACE_ANTHROPIC_ENABLED", "false")
    monkeypatch.setenv("DD_TRACE_BOTOCORE_ENABLED", "false")
    monkeypatch.setenv("DD_PATCH_MODULES", "openai:false")
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable()
        llmobs_patch.assert_called_once()
        kwargs = llmobs_patch.call_args[1]
        for module in SUPPORTED_LLMOBS_INTEGRATIONS.values():
            if module in ("openai", "anthropic", "botocore"):
                assert kwargs[module] is False
                continue
            assert kwargs[module] is True
        llmobs_service.disable()


def test_start_span_with_no_ml_app_defaults_to_service_name(llmobs_no_ml_app):
    with llmobs_no_ml_app.task() as span:
        assert get_llmobs_ml_app(span) == "tests.llmobs"


def test_start_span_empty_ml_app_defaults_to_service_name(llmobs_empty_ml_app):
    with llmobs_empty_ml_app.task() as span:
        assert get_llmobs_ml_app(span) == "tests.llmobs"


def test_start_span_without_ml_app_does_noop():
    with llmobs_service.task():
        pass


def test_ml_app_local_precedence(llmobs, tracer):
    with tracer.trace("apm") as apm_span:
        apm_span.context._meta[PROPAGATED_ML_APP_KEY] = "propagated-ml-app"
        with llmobs.workflow(ml_app="local-ml-app") as span:
            assert get_llmobs_ml_app(span) == "local-ml-app"


def test_ml_app_parent_precedence(llmobs, tracer):
    with tracer.trace("apm") as apm_span:
        apm_span.context._meta[PROPAGATED_ML_APP_KEY] = "propagated-ml-app"
        with llmobs.workflow(ml_app="local-ml-app"):
            with llmobs.workflow() as child_workflow_span:
                assert get_llmobs_ml_app(child_workflow_span) == "local-ml-app"


def test_ml_app_propagated_precedence(llmobs, tracer):
    with tracer.trace("apm") as apm_span:
        apm_span.context._meta[PROPAGATED_ML_APP_KEY] = "propagated-ml-app"
        with llmobs.workflow() as span:
            assert get_llmobs_ml_app(span) == "propagated-ml-app"


def test_ml_app_uses_global_as_default(llmobs):
    with llmobs.workflow() as span:
        assert get_llmobs_ml_app(span) == "unnamed-ml-app"


def test_start_span_writes_ml_app_to_ctx_item(llmobs):
    with llmobs.workflow(ml_app="my-app") as span:
        assert span._get_ctx_item(ML_APP) == "my-app"


def test_start_span_writes_session_id_to_ctx_item(llmobs):
    with llmobs.workflow(session_id="test-session") as span:
        assert span._get_ctx_item(SESSION_ID) == "test-session"


def test_child_span_inherits_ml_app_from_parent_ctx_item(llmobs):
    with llmobs.workflow(ml_app="my-app"):
        with llmobs.task() as child:
            assert get_llmobs_ml_app(child) == "my-app"
            assert child._get_ctx_item(ML_APP) == "my-app"


def test_child_span_inherits_session_id_from_parent_ctx_item(llmobs):
    with llmobs.workflow(session_id="test-session"):
        with llmobs.task() as child:
            assert get_llmobs_session_id(child) == "test-session"
            assert child._get_ctx_item(SESSION_ID) == "test-session"


def test_start_span_while_disabled_logs_warning(llmobs, mock_llmobs_logs):
    llmobs.disable()
    _ = llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_llmobs_logs.reset_mock()
    _ = llmobs.tool(name="test_tool")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_llmobs_logs.reset_mock()
    _ = llmobs.task(name="test_task")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_llmobs_logs.reset_mock()
    _ = llmobs.workflow(name="test_workflow")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)
    mock_llmobs_logs.reset_mock()
    _ = llmobs.agent(name="test_agent")
    mock_llmobs_logs.warning.assert_called_once_with(SPAN_START_WHILE_DISABLED_WARNING)


def test_start_span_uses_kind_as_default_name(llmobs):
    with llmobs.llm(model_name="test_model", model_provider="test_provider") as span:
        assert span.name == "llm"
    with llmobs.tool() as span:
        assert span.name == "tool"
    with llmobs.task() as span:
        assert span.name == "task"
    with llmobs.workflow() as span:
        assert span.name == "workflow"
    with llmobs.agent() as span:
        assert span.name == "agent"


def test_start_span_with_session_id(llmobs):
    with llmobs.llm(model_name="test_model", session_id="test_session_id") as span:
        assert get_llmobs_session_id(span) == "test_session_id"
    with llmobs.tool(session_id="test_session_id") as span:
        assert get_llmobs_session_id(span) == "test_session_id"
    with llmobs.task(session_id="test_session_id") as span:
        assert get_llmobs_session_id(span) == "test_session_id"
    with llmobs.workflow(session_id="test_session_id") as span:
        assert get_llmobs_session_id(span) == "test_session_id"
    with llmobs.agent(session_id="test_session_id") as span:
        assert get_llmobs_session_id(span) == "test_session_id"


def test_session_id_becomes_top_level_field(llmobs):
    session_id = "test_session_id"
    with llmobs.task(session_id=session_id) as span:
        pass
    assert get_llmobs_session_id(span) == session_id


def test_llm_span(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
    assert get_llmobs_span_kind(span) == "llm"
    assert get_llmobs_model_name(span) == "test_model"
    assert get_llmobs_model_provider(span) == "test_provider"


def test_llm_span_no_model_sets_default(llmobs):
    with llmobs.llm(name="test_llm_call", model_provider="test_provider") as span:
        pass
    assert get_llmobs_span_kind(span) == "llm"
    assert get_llmobs_model_name(span) == UNKNOWN_MODEL_NAME
    assert get_llmobs_model_provider(span) == "test_provider"


def test_default_model_provider_set_to_unknown(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call") as span:
        assert span.name == "test_llm_call"
        assert span.resource == "llm"
        assert span.span_type == "llm"
        assert get_llmobs_span_kind(span) == "llm"
        assert get_llmobs_model_name(span) == "test_model"
        assert get_llmobs_model_provider(span) == UNKNOWN_MODEL_PROVIDER


def test_tool_span(llmobs):
    with llmobs.tool(name="test_tool") as span:
        assert span.name == "test_tool"
        assert span.resource == "tool"
        assert span.span_type == "llm"
    assert get_llmobs_span_kind(span) == "tool"


def test_task_span(llmobs):
    with llmobs.task(name="test_task") as span:
        assert span.name == "test_task"
        assert span.resource == "task"
        assert span.span_type == "llm"
    assert get_llmobs_span_kind(span) == "task"


def test_workflow_span(llmobs):
    with llmobs.workflow(name="test_workflow") as span:
        assert span.name == "test_workflow"
        assert span.resource == "workflow"
        assert span.span_type == "llm"
    assert get_llmobs_span_kind(span) == "workflow"


def test_agent_span(llmobs):
    with llmobs.agent(name="test_agent") as span:
        assert span.name == "test_agent"
        assert span.resource == "agent"
        assert span.span_type == "llm"
    assert get_llmobs_span_kind(span) == "agent"


def test_embedding_span_no_model_sets_default(llmobs):
    with llmobs.embedding(name="test_embedding", model_provider="test_provider") as span:
        pass
    assert get_llmobs_span_kind(span) == "embedding"
    assert get_llmobs_model_name(span) == UNKNOWN_MODEL_NAME
    assert get_llmobs_model_provider(span) == "test_provider"


def test_embedding_default_model_provider_set_to_unknown(llmobs):
    with llmobs.embedding(model_name="test_model", name="test_embedding") as span:
        assert span.name == "test_embedding"
        assert span.resource == "embedding"
        assert span.span_type == "llm"
        assert get_llmobs_span_kind(span) == "embedding"
        assert get_llmobs_model_name(span) == "test_model"
        assert get_llmobs_model_provider(span) == UNKNOWN_MODEL_PROVIDER


def test_embedding_span(llmobs):
    with llmobs.embedding(model_name="test_model", name="test_embedding", model_provider="test_provider") as span:
        assert span.name == "test_embedding"
        assert span.resource == "embedding"
        assert span.span_type == "llm"
    assert get_llmobs_span_kind(span) == "embedding"
    assert get_llmobs_model_name(span) == "test_model"
    assert get_llmobs_model_provider(span) == "test_provider"


def test_annotate_no_active_span_logs_warning(llmobs):
    with pytest.raises(Exception) as excinfo:
        llmobs.annotate(metadata={"test": "test"})
    assert str(excinfo.value) == (
        "No span provided and no active LLMObs-generated span found. "
        "Ensure you pass the span explicitly using LLMObs.annotate(span=<your_span>, ...) "
        "when annotating from a different thread or async task than where the span was created."
    )


def test_annotate_non_llm_span_logs_warning(tracer, llmobs):
    with tracer.trace("root") as non_llmobs_span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=non_llmobs_span, metadata={"test": "test"})
        assert str(excinfo.value) == "Span must be an LLMObs-generated span."


def test_annotate_finished_span_does_nothing(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        pass
    with pytest.raises(Exception) as excinfo:
        llmobs.annotate(span=span, metadata={"test": "test"})
    assert str(excinfo.value) == "Cannot annotate a finished span."


def test_annotate_metadata(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, metadata={"temperature": 0.5, "max_tokens": 20, "top_k": 10, "n": 3})
        assert get_llmobs_metadata(span) == {
            "temperature": 0.5,
            "max_tokens": 20,
            "top_k": 10,
            "n": 3,
        }


def test_annotate_metadata_updates(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, metadata={"temperature": 0.5, "max_tokens": 20, "top_k": 10, "n": 3})
        llmobs.annotate(span=span, metadata={"temperature": 1, "logit_bias": [{"1": 2}]})
        assert get_llmobs_metadata(span) == {
            "temperature": 1,
            "max_tokens": 20,
            "top_k": 10,
            "n": 3,
            "logit_bias": [{"1": 2}],
        }


def test_annotate_metadata_wrong_type_raises(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, metadata="wrong_metadata")
        assert str(excinfo.value) == "metadata must be a dictionary"


def test_annotate_tag(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, tags={"test_tag_name": "test_tag_value", "test_numeric_tag": 10})
        # Non-string tag values are coerced to strings at annotation time.
        assert {"test_tag_name": "test_tag_value", "test_numeric_tag": "10"}.items() <= get_llmobs_tags(span).items()


def test_annotate_tag_can_set_session_id(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, tags={"session_id": "1234567890"})
        assert {"session_id": "1234567890"}.items() <= get_llmobs_tags(span).items()
        assert get_llmobs_session_id(span) == "1234567890"


def test_annotate_cost_tags(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(
            span=span,
            tags={"team": "ml", "feature": "chatbot", "debug_id": "abc"},
            cost_tags=["team", "feature"],
        )
        assert get_llmobs_cost_tags(span) == ["team", "feature"]


def test_annotate_cost_tags_dedupes_across_annotations(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, tags={"team": "ml", "feature": "chatbot"}, cost_tags=["team", "feature", "team"])
        llmobs.annotate(span=span, tags={"project": "alpha"}, cost_tags=["feature", "project"])
        assert get_llmobs_cost_tags(span) == ["team", "feature", "project"]


def test_annotate_cost_tags_invalid_entries_are_skipped(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, tags={"team": "ml"}, cost_tags=["team", "missing", 123])
        assert get_llmobs_cost_tags(span) == ["team"]

    mock_llmobs_logs.warning.assert_has_calls(
        [
            mock.call("cost_tags entry %r must reference a key present in span tags. Skipping entry.", "missing"),
            mock.call("cost_tags entries must be strings. Skipping entry %r.", 123),
        ]
    )


def test_annotate_cost_tags_non_list_is_rejected(llmobs, mock_llmobs_logs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, tags={"team": "ml"}, cost_tags="team")
        assert get_llmobs_cost_tags(span) is None

    mock_llmobs_logs.warning.assert_any_call("cost_tags must be a list of strings. Ignoring value.")


def test_annotate_cost_tags_references_existing_span_tags(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, tags={"team": "ml"})
        llmobs.annotate(span=span, cost_tags=["team"])
        assert get_llmobs_cost_tags(span) == ["team"]


def test_annotate_cost_tags_empty_list_is_ignored(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.annotate(span=span, tags={"team": "ml"}, cost_tags=[])
        assert get_llmobs_cost_tags(span) is None


def test_annotate_tag_wrong_type(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, tags=12345)
        assert str(excinfo.value) == "span tags must be a dictionary of string key - primitive value pairs."


def test_annotate_input_string(llmobs):
    with llmobs.llm(model_name="test_model") as llm_span:
        llmobs.annotate(span=llm_span, input_data="test_input")
        assert get_llmobs_input_messages(llm_span) == [{"content": "test_input"}]
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, input_data="test_input")
        assert get_llmobs_input_value(task_span) == "test_input"
    with llmobs.tool() as tool_span:
        llmobs.annotate(span=tool_span, input_data="test_input")
        assert get_llmobs_input_value(tool_span) == "test_input"
    with llmobs.workflow() as workflow_span:
        llmobs.annotate(span=workflow_span, input_data="test_input")
        assert get_llmobs_input_value(workflow_span) == "test_input"
    with llmobs.agent() as agent_span:
        llmobs.annotate(span=agent_span, input_data="test_input")
        assert get_llmobs_input_value(agent_span) == "test_input"
    with llmobs.retrieval() as retrieval_span:
        llmobs.annotate(span=retrieval_span, input_data="test_input")
        assert get_llmobs_input_value(retrieval_span) == "test_input"


def test_annotate_numeric_io(llmobs):
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, input_data=0, output_data=0)
        assert get_llmobs_input_value(task_span) == "0"
        assert get_llmobs_output_value(task_span) == "0"
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, input_data=1.23, output_data=1.23)
        assert get_llmobs_input_value(task_span) == "1.23"
        assert get_llmobs_output_value(task_span) == "1.23"


def test_annotate_input_serializable_value(llmobs):
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, input_data=["test_input"])
        assert get_llmobs_input_value(task_span) == '["test_input"]'
    with llmobs.tool() as tool_span:
        llmobs.annotate(span=tool_span, input_data={"test_input": "hello world"})
        assert get_llmobs_input_value(tool_span) == '{"test_input": "hello world"}'
    with llmobs.workflow() as workflow_span:
        llmobs.annotate(span=workflow_span, input_data=("asd", 123))
        assert get_llmobs_input_value(workflow_span) == '["asd", 123]'
    with llmobs.agent() as agent_span:
        llmobs.annotate(span=agent_span, input_data="test_input")
        assert get_llmobs_input_value(agent_span) == "test_input"
    with llmobs.retrieval() as retrieval_span:
        llmobs.annotate(span=retrieval_span, input_data=[0, 1, 2, 3, 4])
        assert get_llmobs_input_value(retrieval_span) == str([0, 1, 2, 3, 4])


def test_annotate_input_llm_message(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data=[{"content": "test_input", "role": "human"}])
        assert get_llmobs_input_messages(span) == [{"content": "test_input", "role": "human"}]


def test_annotate_input_llm_message_wrong_type(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, input_data=[{"content": object()}])
        assert str(excinfo.value) == "Failed to parse input messages."


def test_llmobs_annotate_incorrect_message_content_type_raises(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, input_data={"role": "user", "content": {"nested": "yes"}})
        assert str(excinfo.value) == "Failed to parse input messages."

        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, output_data={"role": "user", "content": {"nested": "yes"}})
        assert str(excinfo.value) == "Failed to parse output messages."


def test_annotate_input_llm_message_with_role_none_implicit(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data=[{"content": "test_input"}])

        # force the span event to be created - this is where we normalize the role
        llmobs._instance._prepare_llmobs_span_data(span, "llm")
        span_event = llmobs._instance._llmobs_span_event(span)
        assert span_event["meta"]["input"]["messages"] == [{"content": "test_input", "role": ""}]


def test_annotate_input_llm_message_with_role_none_explicit(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data=[{"content": "test_input", "role": None}])
        llmobs._instance._prepare_llmobs_span_data(span, "llm")
        span_event = llmobs._instance._llmobs_span_event(span)
        assert span_event["meta"]["input"]["messages"] == [{"content": "test_input", "role": ""}]


def test_annotate_llm_message_with_audio_parts(llmobs):
    """Audio parts annotated on input/output messages reach the emitted span event."""
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "transcribe this",
                    "role": "user",
                    "audio_parts": [{"mime_type": "audio/wav", "content": "AAAA"}],
                }
            ],
            output_data=[
                {
                    "content": "done",
                    "role": "assistant",
                    "audio_parts": [{"mime_type": "audio/mp3", "content": "BBBB"}],
                }
            ],
        )
        llmobs._instance._prepare_llmobs_span_data(span, "llm")
        span_event = llmobs._instance._llmobs_span_event(span)
        assert span_event["meta"]["input"]["messages"] == [
            {
                "content": "transcribe this",
                "role": "user",
                "audio_parts": [{"mime_type": "audio/wav", "content": "AAAA"}],
            }
        ]
        assert span_event["meta"]["output"]["messages"] == [
            {"content": "done", "role": "assistant", "audio_parts": [{"mime_type": "audio/mp3", "content": "BBBB"}]}
        ]


def test_annotate_llm_message_with_image_parts(llmobs):
    """Image parts annotated on input/output messages reach the emitted span event."""
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "describe this",
                    "role": "user",
                    "image_parts": [{"mime_type": "image/png", "content": "AAAA"}],
                }
            ],
            output_data=[
                {
                    "content": "done",
                    "role": "assistant",
                    "image_parts": [{"mime_type": "image/jpeg", "content": "BBBB"}],
                }
            ],
        )
        llmobs._instance._prepare_llmobs_span_data(span, "llm")
        span_event = llmobs._instance._llmobs_span_event(span)
        assert span_event["meta"]["input"]["messages"] == [
            {
                "content": "describe this",
                "role": "user",
                "image_parts": [{"mime_type": "image/png", "content": "AAAA"}],
            }
        ]
        assert span_event["meta"]["output"]["messages"] == [
            {"content": "done", "role": "assistant", "image_parts": [{"mime_type": "image/jpeg", "content": "BBBB"}]}
        ]


def _agent_span_meta(llmobs, span):
    """Finalize an agent span and return its emitted meta block."""
    llmobs._instance._prepare_llmobs_span_data(span, "agent")
    return llmobs._instance._llmobs_span_event(span)["meta"]


def test_annotate_agent_message_with_image_parts(llmobs):
    """An agent span carrying image parts emits both the collapsed value and typed messages."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "describe this",
                    "role": "user",
                    "image_parts": [{"mime_type": "image/png", "content": "AAAA"}],
                }
            ],
            output_data=[
                {
                    "content": "done",
                    "role": "assistant",
                    "image_parts": [{"mime_type": "image/jpeg", "content": "BBBB"}],
                }
            ],
        )
        meta = _agent_span_meta(llmobs, span)
        assert meta["input"]["messages"] == [
            {"content": "describe this", "role": "user", "image_parts": [{"mime_type": "image/png", "content": "AAAA"}]}
        ]
        assert meta["output"]["messages"] == [
            {"content": "done", "role": "assistant", "image_parts": [{"mime_type": "image/jpeg", "content": "BBBB"}]}
        ]
        # value coexists with messages, and the base64 payload stays out of it
        assert meta["input"]["value"] == "describe this"
        assert meta["output"]["value"] == "done"
        assert "AAAA" not in meta["input"]["value"]
        assert "BBBB" not in meta["output"]["value"]


def test_annotate_agent_message_with_audio_parts(llmobs):
    """Audio parts travel the same agent-span path as image parts."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "transcribe this",
                    "role": "user",
                    "audio_parts": [{"mime_type": "audio/wav", "content": "AAAA"}],
                }
            ],
        )
        meta = _agent_span_meta(llmobs, span)
        assert meta["input"]["messages"] == [
            {
                "content": "transcribe this",
                "role": "user",
                "audio_parts": [{"mime_type": "audio/wav", "content": "AAAA"}],
            }
        ]
        assert meta["input"]["value"] == "transcribe this"


def test_annotate_agent_message_with_image_parts_attachment_key(llmobs):
    """attachment_key media on an agent span survives instead of being stringified."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "",
                    "role": "user",
                    "image_parts": [{"mime_type": "image/jpeg", "attachment_key": "abc123"}],
                }
            ],
        )
        meta = _agent_span_meta(llmobs, span)
        assert meta["input"]["messages"] == [
            {"content": "", "role": "user", "image_parts": [{"mime_type": "image/jpeg", "attachment_key": "abc123"}]}
        ]
        assert meta["input"]["value"] == ""


def test_annotate_agent_message_with_media_multiple_messages_json_value(llmobs):
    """More than one message keeps the JSON value form, with media stripped out of it."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "describe this",
                    "role": "user",
                    "image_parts": [{"mime_type": "image/png", "content": "AAAA"}],
                },
                {"content": "second", "role": "assistant"},
            ],
        )
        meta = _agent_span_meta(llmobs, span)
        assert len(meta["input"]["messages"]) == 2
        assert meta["input"]["messages"][0]["image_parts"] == [{"mime_type": "image/png", "content": "AAAA"}]
        assert (
            meta["input"]["value"]
            == '[{"content": "describe this", "role": "user"}, {"content": "second", "role": "assistant"}]'
        )
        assert "AAAA" not in meta["input"]["value"]


def test_annotate_agent_message_with_media_lone_system_message_json_value(llmobs):
    """A lone system message is not scalarized, matching the trace indexer."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "you are a bot",
                    "role": "system",
                    "image_parts": [{"mime_type": "image/png", "content": "AAAA"}],
                }
            ],
        )
        meta = _agent_span_meta(llmobs, span)
        assert meta["input"]["value"] == '[{"content": "you are a bot", "role": "system"}]'


def test_annotate_agent_message_with_media_and_tool_calls_json_value(llmobs):
    """Tool structure forces the JSON value form so the structure is not dropped."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "call it",
                    "role": "user",
                    "tool_calls": [{"name": "get_weather", "arguments": {"city": "NYC"}}],
                    "image_parts": [{"mime_type": "image/png", "content": "AAAA"}],
                }
            ],
        )
        meta = _agent_span_meta(llmobs, span)
        assert meta["input"]["messages"][0]["image_parts"] == [{"mime_type": "image/png", "content": "AAAA"}]
        assert meta["input"]["value"] == (
            '[{"content": "call it", "role": "user", '
            '"tool_calls": [{"arguments": {"city": "NYC"}, "name": "get_weather"}]}]'
        )


def test_annotate_agent_media_input_leaves_plain_output_untouched(llmobs):
    """Only the media-bearing side routes to messages; the other side keeps value tagging."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "describe this",
                    "role": "user",
                    "image_parts": [{"mime_type": "image/png", "content": "AAAA"}],
                }
            ],
            output_data="all done",
        )
        meta = _agent_span_meta(llmobs, span)
        assert meta["input"]["messages"][0]["image_parts"] == [{"mime_type": "image/png", "content": "AAAA"}]
        assert "messages" not in meta["output"]
        assert meta["output"]["value"] == "all done"


def test_annotate_agent_message_without_media_unchanged(llmobs):
    """Regression pin: a media-free agent span emits value only, exactly as before."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=[{"content": "hello", "role": "user"}],
            output_data=[{"content": "hi", "role": "assistant"}],
        )
        meta = _agent_span_meta(llmobs, span)
        assert "messages" not in meta["input"]
        assert "messages" not in meta["output"]
        assert meta["input"]["value"] == '[{"content": "hello", "role": "user"}]'
        assert meta["output"]["value"] == '[{"content": "hi", "role": "assistant"}]'


def test_annotate_agent_plain_string_and_dict_unchanged(llmobs):
    """Regression pin: non-message agent input is untouched by the media path."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(span=span, input_data="just a string", output_data={"some": "dict"})
        meta = _agent_span_meta(llmobs, span)
        assert "messages" not in meta["input"]
        assert "messages" not in meta["output"]
        assert meta["input"]["value"] == "just a string"
        assert meta["output"]["value"] == '{"some": "dict"}'


def test_annotate_agent_empty_media_lists_unchanged(llmobs):
    """Regression pin: empty media lists are not media, so nothing reroutes."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(span=span, input_data=[{"content": "hello", "role": "user", "image_parts": []}])
        meta = _agent_span_meta(llmobs, span)
        assert "messages" not in meta["input"]
        assert meta["input"]["value"] == '[{"content": "hello", "image_parts": [], "role": "user"}]'


@pytest.mark.parametrize(
    "span_kind,expected_value",
    [
        # workflow, task and step collapse a lone plain message to bare text; tool is not a
        # scalar-value kind, so it keeps the JSON form. Either way the base64 stays out.
        ("workflow", "describe this"),
        ("task", "describe this"),
        ("tool", '[{"content": "describe this", "role": "user"}]'),
    ],
)
def test_annotate_non_agent_kinds_with_media_keep_messages(llmobs, span_kind, expected_value):
    """Media on workflow, task and tool spans survives as typed messages.

    The serving API populates messages for these kinds through defaultSpanFromEvent, so the
    typed parts render instead of being stringified into the value.
    """
    with getattr(llmobs, span_kind)(name="test_span") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "describe this",
                    "role": "user",
                    "image_parts": [{"mime_type": "image/png", "content": "AAAA"}],
                }
            ],
        )
        llmobs._instance._prepare_llmobs_span_data(span, span_kind)
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
        assert meta["input"]["messages"] == [
            {"content": "describe this", "role": "user", "image_parts": [{"mime_type": "image/png", "content": "AAAA"}]}
        ]
        assert meta["input"]["value"] == expected_value
        assert "AAAA" not in meta["input"]["value"]


@pytest.mark.parametrize("span_kind", ["workflow", "task", "tool"])
def test_annotate_non_agent_kinds_without_media_unchanged(llmobs, span_kind):
    """Regression pin: widening the media set must not change media-free non-agent spans."""
    with getattr(llmobs, span_kind)(name="test_span") as span:
        llmobs.annotate(span=span, input_data=[{"content": "hello", "role": "user"}])
        llmobs._instance._prepare_llmobs_span_data(span, span_kind)
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
        assert "messages" not in meta["input"]


def test_annotate_agent_message_malformed_image_parts_raises(llmobs):
    """A media part that is a dict but missing required keys still reports through Messages.

    Only non-dict parts are diverted back to the value path; a dict part is a genuine attempt at
    media and its validation error is worth surfacing.
    """
    with llmobs.agent(name="test_agent") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, input_data=[{"content": "x", "image_parts": [{"content": "AAAA"}]}])
        assert str(excinfo.value) == "Failed to parse input messages."


@pytest.mark.parametrize("span_kind", ["agent", "workflow", "task", "tool"])
@pytest.mark.parametrize("media_key", ["image_parts", "audio_parts"])
@pytest.mark.parametrize("parts", [["/tmp/a.png"], [None], [1], ["a", "b"]])
def test_annotate_non_dict_media_parts_do_not_raise(llmobs, span_kind, media_key, parts):
    """A media key holding non-dict entries stays on the value path instead of raising.

    Routing it to Messages() would raise TypeError, which annotate converts into an
    LLMObsAnnotateSpanError in the caller's own code.
    """
    with getattr(llmobs, span_kind)(name="test_span") as span:
        llmobs.annotate(span=span, input_data=[{"content": "hello", "role": "user", media_key: parts}])
        llmobs._instance._prepare_llmobs_span_data(span, span_kind)
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert "messages" not in meta["input"]
    assert "hello" in meta["input"]["value"]


@pytest.mark.parametrize("span_kind", ["agent", "workflow", "task", "tool"])
def test_annotate_value_after_media_messages_overrides(llmobs, span_kind):
    """A later value annotation wins and leaves no media behind.

    annotate is documented as last-write-wins. meta.input persists across calls and the
    emit path prefers messages over value, so the value write has to clear its sibling or
    a caller re-annotating to redact would still ship the original payload.
    """
    media = [{"content": "SECRET", "role": "user", "image_parts": [{"mime_type": "image/png", "content": "QkFTRTY0"}]}]
    with getattr(llmobs, span_kind)(name="test_span") as span:
        llmobs.annotate(span=span, input_data=media)
        llmobs.annotate(span=span, input_data="redacted")
        llmobs._instance._prepare_llmobs_span_data(span, span_kind)
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert meta["input"]["value"] == "redacted"
    assert "messages" not in meta["input"]
    assert "QkFTRTY0" not in str(meta["input"])


@pytest.mark.parametrize("span_kind", ["agent", "workflow", "task", "tool"])
def test_annotate_media_messages_after_value_overrides(llmobs, span_kind):
    """The reverse order also wins, rather than working by accident."""
    media = [{"content": "hello", "role": "user", "image_parts": [{"mime_type": "image/png", "content": "QUJD"}]}]
    with getattr(llmobs, span_kind)(name="test_span") as span:
        llmobs.annotate(span=span, input_data="first")
        llmobs.annotate(span=span, input_data=media)
        llmobs._instance._prepare_llmobs_span_data(span, span_kind)
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert meta["input"]["messages"] == media
    assert "first" not in str(meta["input"])


@pytest.mark.parametrize("span_kind", ["agent", "workflow", "task", "tool"])
def test_annotate_media_value_omitted_past_size_limit(llmobs, span_kind):
    """The derived value is dropped rather than costing the span its whole input and output.

    messages and the collapsed value both carry the text, so the pair can exceed the event limit
    where messages alone would not. Past the limit the writer replaces input and output wholesale,
    which would discard the media this field exists to accompany.
    """
    media = [
        {"content": "x" * 4_000, "role": "user", "image_parts": [{"mime_type": "image/png", "content": "A" * 4_000}]}
    ]
    with override_global_config(dict(_llmobs_event_size_limit=10_000)):
        with getattr(llmobs, span_kind)(name="test_span") as span:
            llmobs.annotate(span=span, input_data=media)
            llmobs._instance._prepare_llmobs_span_data(span, span_kind)
            meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert meta["input"]["value"] == "[value omitted: event size limit]"
    assert meta["input"]["messages"] == media


@pytest.mark.parametrize("span_kind", ["agent", "workflow", "task", "tool"])
def test_annotate_media_value_kept_within_size_limit(llmobs, span_kind):
    """Regression pin: the size guard must not fire on ordinary payloads."""
    media = [{"content": "hello", "role": "user", "image_parts": [{"mime_type": "image/png", "content": "QkFTRTY0"}]}]
    with getattr(llmobs, span_kind)(name="test_span") as span:
        llmobs.annotate(span=span, input_data=media)
        llmobs._instance._prepare_llmobs_span_data(span, span_kind)
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert "omitted" not in meta["input"]["value"]
    assert "hello" in meta["input"]["value"]


def test_annotate_output_non_dict_media_parts_do_not_raise(llmobs):
    """The output side takes the same value-path fallback as the input side."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(span=span, output_data=[{"content": "x", "image_parts": ["not-a-dict"]}])
        llmobs._instance._prepare_llmobs_span_data(span, "agent")
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert "messages" not in meta["output"]
    assert "x" in meta["output"]["value"]


def test_annotate_llm_message_without_media_unchanged(llmobs):
    """Regression pin: LLM spans still emit messages only, with no value alongside."""
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data=[{"content": "hello", "role": "user"}])
        llmobs._instance._prepare_llmobs_span_data(span, "llm")
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
        assert meta["input"]["messages"] == [{"content": "hello", "role": "user"}]
        assert "value" not in meta["input"]


def test_annotate_document_str(llmobs):
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data="test_document_text")
        documents = get_llmobs_input_documents(span)
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"
    with llmobs.retrieval() as span:
        llmobs.annotate(span=span, output_data="test_document_text")
        documents = get_llmobs_output_documents(span)
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"


def test_annotate_document_dict(llmobs):
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data={"text": "test_document_text"})
        documents = get_llmobs_input_documents(span)
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"
    with llmobs.retrieval() as span:
        llmobs.annotate(span=span, output_data={"text": "test_document_text"})
        documents = get_llmobs_output_documents(span)
        assert documents
        assert len(documents) == 1
        assert documents[0]["text"] == "test_document_text"


def test_annotate_document_list(llmobs):
    with llmobs.embedding(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            input_data=[{"text": "test_document_text"}, {"text": "text", "name": "name", "score": 0.9, "id": "id"}],
        )
        documents = get_llmobs_input_documents(span)
        assert documents
        assert len(documents) == 2
        assert documents[0]["text"] == "test_document_text"
        assert documents[1]["text"] == "text"
        assert documents[1]["name"] == "name"
        assert documents[1]["id"] == "id"
        assert documents[1]["score"] == 0.9
    with llmobs.retrieval() as span:
        llmobs.annotate(
            span=span,
            output_data=[{"text": "test_document_text"}, {"text": "text", "name": "name", "score": 0.9, "id": "id"}],
        )
        documents = get_llmobs_output_documents(span)
        assert documents
        assert len(documents) == 2
        assert documents[0]["text"] == "test_document_text"
        assert documents[1]["text"] == "text"
        assert documents[1]["name"] == "name"
        assert documents[1]["id"] == "id"
        assert documents[1]["score"] == 0.9


def test_annotate_incorrect_document_type_raises(llmobs):
    with llmobs.embedding(model_name="test_model") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, input_data={"text": 123})
        assert str(excinfo.value) == "Failed to parse input documents."
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, input_data=123)
        assert str(excinfo.value) == "Failed to parse input documents."
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, input_data=object())
        assert str(excinfo.value) == "Failed to parse input documents."
    with llmobs.retrieval() as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, output_data=[{"score": 0.9, "id": "id", "name": "name"}])
        assert str(excinfo.value) == "Failed to parse output documents."
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, output_data=123)
        assert str(excinfo.value) == "Failed to parse output documents."
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, output_data=object())
        assert str(excinfo.value) == "Failed to parse output documents."


def test_annotate_document_no_text_raises(llmobs):
    with llmobs.embedding(model_name="test_model") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, input_data=[{"score": 0.9, "id": "id", "name": "name"}])
        assert str(excinfo.value) == "Failed to parse input documents."
    with llmobs.retrieval() as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, output_data=[{"score": 0.9, "id": "id", "name": "name"}])
        assert str(excinfo.value) == "Failed to parse output documents."


def test_annotate_incorrect_document_field_type_raises(llmobs):
    with llmobs.embedding(model_name="test_model") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, input_data=[{"text": "test_document_text", "score": "0.9"}])
        assert str(excinfo.value) == "Failed to parse input documents."
    with llmobs.embedding(model_name="test_model") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(
                span=span, input_data=[{"text": "text", "id": 123, "score": "0.9", "name": ["h", "e", "l", "l", "o"]}]
            )
        assert str(excinfo.value) == "Failed to parse input documents."
    with llmobs.retrieval() as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, output_data=[{"text": "test_document_text", "score": "0.9"}])
        assert str(excinfo.value) == "Failed to parse output documents."
    with llmobs.retrieval() as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(
                span=span, output_data=[{"text": "text", "id": 123, "score": "0.9", "name": ["h", "e", "l", "l", "o"]}]
            )
        assert str(excinfo.value) == "Failed to parse output documents."


def test_annotate_output_string(llmobs):
    with llmobs.llm(model_name="test_model") as llm_span:
        llmobs.annotate(span=llm_span, output_data="test_output")
        assert get_llmobs_output_messages(llm_span) == [{"content": "test_output"}]
    with llmobs.embedding(model_name="test_model") as embedding_span:
        llmobs.annotate(span=embedding_span, output_data="test_output")
        assert get_llmobs_output_value(embedding_span) == "test_output"
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, output_data="test_output")
        assert get_llmobs_output_value(task_span) == "test_output"
    with llmobs.tool() as tool_span:
        llmobs.annotate(span=tool_span, output_data="test_output")
        assert get_llmobs_output_value(tool_span) == "test_output"
    with llmobs.workflow() as workflow_span:
        llmobs.annotate(span=workflow_span, output_data="test_output")
        assert get_llmobs_output_value(workflow_span) == "test_output"
    with llmobs.agent() as agent_span:
        llmobs.annotate(span=agent_span, output_data="test_output")
        assert get_llmobs_output_value(agent_span) == "test_output"


def test_annotate_output_serializable_value(llmobs):
    with llmobs.embedding(model_name="test_model") as embedding_span:
        llmobs.annotate(span=embedding_span, output_data=[[0, 1, 2, 3], [4, 5, 6, 7]])
        assert get_llmobs_output_value(embedding_span) == "[[0, 1, 2, 3], [4, 5, 6, 7]]"
    with llmobs.task() as task_span:
        llmobs.annotate(span=task_span, output_data=["test_output"])
        assert get_llmobs_output_value(task_span) == '["test_output"]'
    with llmobs.tool() as tool_span:
        llmobs.annotate(span=tool_span, output_data={"test_output": "hello world"})
        assert get_llmobs_output_value(tool_span) == '{"test_output": "hello world"}'
    with llmobs.workflow() as workflow_span:
        llmobs.annotate(span=workflow_span, output_data=("asd", 123))
        assert get_llmobs_output_value(workflow_span) == '["asd", 123]'
    with llmobs.agent() as agent_span:
        llmobs.annotate(span=agent_span, output_data="test_output")
        assert get_llmobs_output_value(agent_span) == "test_output"


def test_annotate_output_llm_message(llmobs):
    with llmobs.llm(model_name="test_model") as llm_span:
        llmobs.annotate(span=llm_span, output_data=[{"content": "test_output", "role": "human"}])
        assert get_llmobs_output_messages(llm_span) == [{"content": "test_output", "role": "human"}]


def test_annotate_output_llm_message_wrong_type(llmobs):
    with llmobs.llm(model_name="test_model") as llm_span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=llm_span, output_data=[{"content": object()}])
        assert str(excinfo.value) == "Failed to parse output messages."
        assert get_llmobs_output_messages(llm_span) is None


def test_annotate_metrics(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, metrics={"input_tokens": 10, "output_tokens": 20, "total_tokens": 30})
        assert get_llmobs_metrics(span) == {
            "input_tokens": 10,
            "output_tokens": 20,
            "total_tokens": 30,
        }


def test_annotate_metrics_updates(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, metrics={"input_tokens": 10, "output_tokens": 20})
        llmobs.annotate(span=span, metrics={"input_tokens": 20, "total_tokens": 40})
        assert get_llmobs_metrics(span) == {
            "input_tokens": 20,
            "output_tokens": 20,
            "total_tokens": 40,
        }


def test_annotate_metrics_dotted_keys_sanitized(llmobs):
    """Dots in metric keys are replaced with underscores so ingestion doesn't nest and drop them."""
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            metrics={"anomaly.query_count": 8, "anomaly.query_error_count": 0, "total_tokens": 30},
        )
        assert get_llmobs_metrics(span) == {
            "anomaly_query_count": 8,
            "anomaly_query_error_count": 0,
            "total_tokens": 30,
        }


def test_annotate_metrics_wrong_type(llmobs):
    with llmobs.llm(model_name="test_model") as llm_span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=llm_span, metrics=12345)
        assert str(excinfo.value) == "metrics must be a dictionary of string key - numeric value pairs."


def test_annotate_prompt_dict(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            prompt={
                "template": "{var1} {var3}",
                "variables": {"var1": "var1", "var2": "var3"},
                "version": "1.0.0",
                "id": "test_prompt",
            },
        )
        assert get_llmobs_input_prompt(span) == {
            "template": "{var1} {var3}",
            "variables": {"var1": "var1", "var2": "var3"},
            "version": "1.0.0",
            "id": "test_prompt",
            "ml_app": "unnamed-ml-app",
            "_dd_context_variable_keys": ["context"],
            "_dd_query_variable_keys": ["question"],
        }
        assert {PROMPT_TRACKING_INSTRUMENTATION_METHOD: "annotated"}.items() <= get_llmobs_tags(span).items()


def test_annotate_prompt_dict_with_context_var_keys(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            prompt={
                "template": "{var1} {var3}",
                "variables": {"var1": "var1", "var2": "var3"},
                "version": "1.0.0",
                "id": "test_prompt",
                "rag_context_variables": ["var1", "var2"],
                "rag_query_variables": ["user_input"],
            },
        )
        assert get_llmobs_input_prompt(span) == {
            "template": "{var1} {var3}",
            "variables": {"var1": "var1", "var2": "var3"},
            "version": "1.0.0",
            "id": "test_prompt",
            "ml_app": "unnamed-ml-app",
            "_dd_context_variable_keys": ["var1", "var2"],
            "_dd_query_variable_keys": ["user_input"],
        }
        assert {PROMPT_TRACKING_INSTRUMENTATION_METHOD: "annotated"}.items() <= get_llmobs_tags(span).items()


def test_annotate_prompt_typed_dict(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(
            span=span,
            prompt=Prompt(
                template="{var1} {var3}",
                variables={"var1": "var1", "var2": "var3"},
                version="1.0.0",
                id="test_prompt",
                rag_context_variables=["var1", "var2"],
                rag_query_variables=["user_input"],
            ),
        )
        assert get_llmobs_input_prompt(span) == {
            "template": "{var1} {var3}",
            "variables": {"var1": "var1", "var2": "var3"},
            "version": "1.0.0",
            "id": "test_prompt",
            "ml_app": "unnamed-ml-app",
            "_dd_context_variable_keys": ["var1", "var2"],
            "_dd_query_variable_keys": ["user_input"],
        }
        assert {PROMPT_TRACKING_INSTRUMENTATION_METHOD: "annotated"}.items() <= get_llmobs_tags(span).items()


def test_annotate_prompt_wrong_type(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, prompt="prompt")
        assert excinfo.value.args == (
            "Failed to validate prompt with error:",
            "Prompt must be a dictionary, received str.",
        )

        with pytest.raises(Exception) as excinfo:
            llmobs.annotate(span=span, prompt={"template": 1})
        assert excinfo.value.args == (
            "Failed to validate prompt with error:",
            "template: 1 must be a string, received int",
        )


def test_annotate_linked_spans(llmobs):
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, _linked_spans=[{"span_id": "123", "trace_id": "456"}])
        assert get_llmobs_span_links(span) == [
            {"span_id": "123", "trace_id": "456", "attributes": {"from": "output", "to": "input"}}
        ]


def test_span_error_sets_error(llmobs):
    with pytest.raises(ValueError):
        with llmobs.llm(model_name="test_model", model_provider="test_model_provider") as span:
            raise ValueError("test error message")
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="test_model",
        model_provider="test_model_provider",
        error={
            "type": "builtins.ValueError",
            "message": "test error message",
            "stack": span.get_tag("error.stack"),
        },
    )


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(version="1.2.3", env="test_env", service="test_service", _llmobs_ml_app="test_app_name")],
)
def test_tags(ddtrace_global_config, llmobs, monkeypatch):
    with llmobs.task(name="test_task") as span:
        pass
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="task",
        tags={"version": "1.2.3", "env": "test_env", "service": "test_service", "ml_app": "test_app_name"},
    )


@pytest.mark.subprocess(
    env={
        "DD_API_KEY": "<not-a-real-key>",
        "DD_LLMOBS_AGENTLESS_ENABLED": "1",
        "DD_LLMOBS_ML_APP": "test-ml-app",
    },
    err=None,
)
def test_tag_dot_keys_sanitized_on_agentless_apm_path():
    """APM agentless path: dots in tag keys are replaced with underscores before encoding."""
    from ddtrace.llmobs import LLMObs as llmobs_service
    from ddtrace.llmobs._utils import get_llmobs_tags

    llmobs_service.enable()
    with llmobs_service.task(name="test_task") as span:
        pass
    tags = get_llmobs_tags(span)
    assert all("." not in k for k in tags), f"Dot in tag key on agentless path: {tags}"
    assert tags is not None and "ddtrace_version" in tags
    llmobs_service.disable()


@pytest.mark.subprocess(
    env={
        "DD_APM_TRACING_ENABLED": "false",
        "DD_LLMOBS_ML_APP": "test-ml-app",
        "DD_API_KEY": "<not-a-real-key>",
    },
    err=None,
)
def test_tag_dot_keys_preserved_on_direct_llmobs_path():
    """LLMOBS_AGENT_PROXY/LLMOBS_AGENTLESS path (DD_APM_TRACING_ENABLED=false): dots in tag keys are not modified."""
    from ddtrace.llmobs import LLMObs as llmobs_service
    from ddtrace.llmobs._utils import get_llmobs_tags

    llmobs_service.enable()
    with llmobs_service.task(name="test_task") as span:
        tags = get_llmobs_tags(span)
    assert tags is not None and "ddtrace.version" in tags
    llmobs_service.disable()


@pytest.mark.subprocess(
    env={
        "DD_LLMOBS_AGENTLESS_ENABLED": "0",
        "DD_LLMOBS_ML_APP": "test-ml-app",
    },
    err=None,
)
def test_tag_dot_keys_preserved_on_apm_agent_path():
    """APM_AGENT path: dots in tag keys are not modified (agent handles encoding)."""
    from ddtrace.llmobs import LLMObs as llmobs_service
    from ddtrace.llmobs._utils import get_llmobs_tags

    llmobs_service.enable(agentless_enabled=False)
    with llmobs_service.task(name="test_task") as span:
        pass
    tags = get_llmobs_tags(span)
    assert tags is not None and "ddtrace.version" in tags
    llmobs_service.disable()


def test_ml_app_override(llmobs):
    with llmobs.task(name="test_task", ml_app="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="task", tags={"ml_app": "test_app"})
    with llmobs.tool(name="test_tool", ml_app="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="tool", tags={"ml_app": "test_app"})
    with llmobs.llm(model_name="model_name", name="test_llm", ml_app="test_app") as span:
        pass
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="model_name",
        model_provider=UNKNOWN_MODEL_PROVIDER,
        tags={"ml_app": "test_app"},
    )
    with llmobs.embedding(model_name="model_name", name="test_embedding", ml_app="test_app") as span:
        pass
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="embedding",
        model_name="model_name",
        model_provider=UNKNOWN_MODEL_PROVIDER,
        tags={"ml_app": "test_app"},
    )
    with llmobs.workflow(name="test_workflow", ml_app="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="workflow", tags={"ml_app": "test_app"})
    with llmobs.agent(name="test_agent", ml_app="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="agent", tags={"ml_app": "test_app"})
    with llmobs.retrieval(name="test_retrieval", ml_app="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="retrieval", tags={"ml_app": "test_app"})


def test_agent_service_override(llmobs):
    with llmobs.task(name="test_task", ml_app="legacy_app", agent_service="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="task", tags={"ml_app": "test_app"})
    with llmobs.tool(name="test_tool", agent_service="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="tool", tags={"ml_app": "test_app"})
    with llmobs.llm(model_name="model_name", name="test_llm", agent_service="test_app") as span:
        pass
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="llm",
        model_name="model_name",
        model_provider=UNKNOWN_MODEL_PROVIDER,
        tags={"ml_app": "test_app"},
    )
    with llmobs.embedding(model_name="model_name", name="test_embedding", agent_service="test_app") as span:
        pass
    assert_llmobs_span_data(
        _get_llmobs_data_metastruct(span),
        span_kind="embedding",
        model_name="model_name",
        model_provider=UNKNOWN_MODEL_PROVIDER,
        tags={"ml_app": "test_app"},
    )
    with llmobs.workflow(name="test_workflow", agent_service="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="workflow", tags={"ml_app": "test_app"})
    with llmobs.agent(name="test_agent", agent_service="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="agent", tags={"ml_app": "test_app"})
    with llmobs.retrieval(name="test_retrieval", agent_service="test_app") as span:
        pass
    assert_llmobs_span_data(_get_llmobs_data_metastruct(span), span_kind="retrieval", tags={"ml_app": "test_app"})


def test_agent_service_tag_mirrors_ml_app(llmobs):
    """Every span carries an agent_service tag that always equals the ml_app tag, including overrides."""
    # Inherited/default identity: agent_service mirrors whatever ml_app resolves to.
    with llmobs.workflow(name="inherited") as span:
        pass
    tags = get_llmobs_tags(span)
    assert tags["agent_service"] == tags["ml_app"]
    # Explicit agent_service overrides a legacy ml_app on both tags (no stale agent_service value).
    with llmobs.task(name="override", ml_app="legacy_app", agent_service="test_app") as span:
        pass
    tags = get_llmobs_tags(span)
    assert tags["ml_app"] == "test_app"
    assert tags["agent_service"] == "test_app"


def test_export_span_specified_span_is_incorrect_type_raises(llmobs):
    with pytest.raises(Exception) as excinfo:
        llmobs.export_span(span="asd")
    assert str(excinfo.value) == "Failed to export span. Span must be a valid Span object."


def test_export_span_specified_span_is_not_llmobs_span_raises(tracer, llmobs):
    with tracer.trace("non_llmobs_span") as span:
        with pytest.raises(Exception) as excinfo:
            llmobs.export_span(span=span)
        assert str(excinfo.value) == "Span must be an LLMObs-generated span."


def test_export_span_specified_span_returns_span_context(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        span_context = llmobs.export_span(span=span)
        assert span_context is not None
        assert span_context["span_id"] == str(span.span_id)
        assert span_context["trace_id"] == get_llmobs_trace_id(span)


def test_export_span_no_specified_span_no_active_span_raises(llmobs):
    with pytest.raises(Exception) as excinfo:
        llmobs.export_span()
    assert str(excinfo.value) == (
        "No span provided and no active LLMObs-generated span found. "
        "Ensure you pass the span explicitly using LLMObs.export_span(span=<your_span>) "
        "when exporting from a different thread or async task than where the span was created."
    )


def test_export_span_active_span_not_llmobs_span_raises(llmobs):
    with llmobs._instance.tracer.trace("non_llmobs_span"):
        with pytest.raises(Exception) as excinfo:
            llmobs.export_span()
        assert str(excinfo.value) == (
            "No span provided and no active LLMObs-generated span found. "
            "Ensure you pass the span explicitly using LLMObs.export_span(span=<your_span>) "
            "when exporting from a different thread or async task than where the span was created."
        )


def test_export_span_no_specified_span_returns_exported_active_span(llmobs):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        span_context = llmobs.export_span()
        assert span_context is not None
        assert span_context["span_id"] == str(span.span_id)
        assert span_context["trace_id"] == get_llmobs_trace_id(span)


def test_flush_does_not_call_periodic_when_llmobs_is_disabled(
    llmobs,
    mock_llmobs_eval_metric_writer,
    mock_llmobs_evaluator_runner,
    mock_llmobs_logs,
):
    llmobs.enabled = False
    llmobs.flush()
    mock_llmobs_eval_metric_writer.periodic.assert_not_called()
    mock_llmobs_evaluator_runner.periodic.assert_not_called()
    mock_llmobs_logs.warning.assert_has_calls(
        [mock.call("flushing when LLMObs is disabled. No spans or evaluation metrics will be sent.")]
    )
    llmobs.enabled = True


def test_inject_distributed_headers_llmobs_disabled_does_nothing(llmobs, mock_llmobs_logs):
    llmobs.disable()
    headers = llmobs.inject_distributed_headers({}, span=None)
    mock_llmobs_logs.warning.assert_called_once_with(
        "LLMObs.inject_distributed_headers() called when LLMObs is not enabled. "
        "Distributed context will not be injected."
    )
    assert headers == {}


@pytest.mark.parametrize("request_headers", ["not a dictionary", 123, None])
def test_inject_distributed_headers_not_dict_logs_warning(llmobs, request_headers):
    with pytest.raises(Exception) as excinfo:
        llmobs.inject_distributed_headers(request_headers, span=None)
    assert str(excinfo.value) == "request_headers must be a dictionary of string key-value pairs."


def test_inject_distributed_headers_no_active_span_logs_warning(llmobs):
    with pytest.raises(Exception) as excinfo:
        llmobs.inject_distributed_headers({}, span=None)
    assert str(excinfo.value) == "No span provided and no currently active span found."


def test_inject_distributed_headers_span_calls_httppropagator_inject(llmobs, mock_llmobs_logs):
    span = llmobs._instance.tracer.trace("test_span")
    with mock.patch("ddtrace.propagation.http.HTTPPropagator.inject") as mock_inject:
        llmobs.inject_distributed_headers({}, span=span)
        assert mock_inject.call_count == 1
        mock_inject.assert_called_once_with(span.context, {})


def test_inject_distributed_headers_current_active_span_injected(llmobs, mock_llmobs_logs):
    span = llmobs.workflow("test_span")
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.inject") as mock_inject:
        llmobs.inject_distributed_headers({}, span=None)
        assert mock_inject.call_count == 1
        mock_inject.assert_called_once_with(span.context, {})


def test_activate_distributed_headers_llmobs_disabled_does_nothing(llmobs, mock_llmobs_logs):
    llmobs.disable()
    llmobs.activate_distributed_headers({})
    mock_llmobs_logs.warning.assert_called_once_with(
        "LLMObs.activate_distributed_headers() called when LLMObs is not enabled. "
        "Distributed context will not be activated."
    )


def test_activate_distributed_headers_calls_httppropagator_extract(llmobs, mock_llmobs_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        llmobs.activate_distributed_headers({})
        assert mock_extract.call_count == 1
        mock_extract.assert_called_once_with({})


def test_activate_distributed_headers_no_trace_id_raises(llmobs):
    with pytest.raises(Exception) as excinfo:
        llmobs.activate_distributed_headers({})
    assert str(excinfo.value) == "Failed to extract trace/span ID from request headers."


def test_activate_distributed_headers_no_span_id_raises(llmobs):
    with pytest.raises(Exception) as excinfo:
        llmobs.activate_distributed_headers({})
    assert str(excinfo.value) == "Failed to extract trace/span ID from request headers."


def test_activate_distributed_headers_no_llmobs_parent_id_does_nothing(llmobs, mock_llmobs_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        dummy_context = Context(trace_id=123, span_id=456)
        mock_extract.return_value = dummy_context
        llmobs.activate_distributed_headers({})
        mock_llmobs_logs.debug.assert_called_once_with("Failed to extract LLMObs parent ID from request headers.")


def test_activate_distributed_headers_no_llmobs_trace_id_starts_new_context(llmobs, mock_llmobs_logs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        dummy_context = Context(
            trace_id=123, span_id=456, meta={PROPAGATED_PARENT_ID_KEY: "123", PROPAGATED_LLMOBS_TRACE_ID_KEY: None}
        )
        mock_extract.return_value = dummy_context
        # Patch the whole context_provider (a native pyclass whose methods are read-only)
        # rather than its `activate` method, so we can still observe the activate call.
        with mock.patch("ddtrace.llmobs.LLMObs._instance.tracer.context_provider") as mock_provider:
            llmobs.activate_distributed_headers({})
            assert mock_extract.call_count == 1
            mock_llmobs_logs.debug.assert_called_once_with(
                "Failed to extract LLMObs trace ID from request headers. Expected string, got None. "
                "Defaulting to the corresponding APM trace ID."
            )
            mock_provider.activate.assert_called_once_with(dummy_context)


def test_activate_distributed_headers_activates_context(llmobs):
    with mock.patch("ddtrace.llmobs._llmobs.HTTPPropagator.extract") as mock_extract:
        dummy_context = Context(trace_id=123, span_id=456, meta={PROPAGATED_PARENT_ID_KEY: "123"})
        mock_extract.return_value = dummy_context
        # Patch the whole context_provider (a native pyclass whose methods are read-only)
        # rather than its `activate` method, so we can still observe the activate call.
        with mock.patch("ddtrace.llmobs.LLMObs._instance.tracer.context_provider") as mock_provider:
            llmobs.activate_distributed_headers({})
            assert mock_extract.call_count == 1
            mock_provider.activate.assert_called_once_with(dummy_context)


def test_listener_hooks_enqueue_correct_writer(run_python_code_in_subprocess):
    """
    Regression test that ensures that listener hooks enqueue span events to the correct writer,
    not the default writer created at startup.
    """
    env = os.environ.copy()
    pypath = [os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))]
    if "PYTHONPATH" in env:
        pypath.append(env["PYTHONPATH"])
    env.update({"PYTHONPATH": ":".join(pypath), "DD_TRACE_ENABLED": "0"})
    out, err, status, pid = run_python_code_in_subprocess(
        """
import mock
import sys
import time
from ddtrace.llmobs import LLMObs

LLMObs.enable(ml_app="repro-issue", agentless_enabled=True, api_key="foobar.baz", site="datad0g.com")
assert LLMObs._instance._llmobs_span_writer._url == "https://llmobs-intake.datad0g.com/api/v2/llmobs"
""",
        env=env,
    )
    assert status == 0, err


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_llmobs_fork_recreates_and_restarts_span_writer():
    """Test that forking a process correctly recreates and restarts the LLMObsSpanWriter."""
    import os

    import mock

    import ddtrace
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.llmobs import LLMObs as llmobs_service

    with mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload"):
        llmobs_service.enable(_tracer=ddtrace.tracer, ml_app="test_app", agentless_enabled=False)
        original_span_writer = llmobs_service._instance._llmobs_span_writer
        pid = os.fork()
        if pid:  # parent
            assert llmobs_service._instance._llmobs_span_writer == original_span_writer
            assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
        else:  # child
            assert llmobs_service._instance._llmobs_span_writer != original_span_writer
            assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_llmobs_fork_recreates_and_restarts_agentless_span_writer():
    """Test that forking a process correctly recreates and restarts the LLMObsSpanWriter."""
    import os

    import mock

    import ddtrace
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.llmobs import LLMObs as llmobs_service

    with mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter._send_payload"):
        llmobs_service.enable(
            _tracer=ddtrace.tracer, ml_app="test_app", agentless_enabled=True, api_key="<not-a-real-key>"
        )
        original_span_writer = llmobs_service._instance._llmobs_span_writer
        pid = os.fork()
        if pid:  # parent
            assert llmobs_service._instance._llmobs_span_writer == original_span_writer
            assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
        else:  # child
            assert llmobs_service._instance._llmobs_span_writer != original_span_writer
            assert llmobs_service._instance._llmobs_span_writer.status == ServiceStatus.RUNNING
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_llmobs_fork_recreates_and_restarts_eval_metric_writer():
    """Test that forking a process correctly recreates and restarts the LLMObsEvalMetricWriter."""
    import os

    import mock

    import ddtrace
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.llmobs import LLMObs as llmobs_service

    with mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter.periodic"):
        llmobs_service.enable(_tracer=ddtrace.tracer, ml_app="test_app")
        original_eval_metric_writer = llmobs_service._instance._llmobs_eval_metric_writer
        pid = os.fork()
        if pid:  # parent
            assert llmobs_service._instance._llmobs_eval_metric_writer == original_eval_metric_writer
            assert llmobs_service._instance._llmobs_eval_metric_writer.status == ServiceStatus.RUNNING
        else:  # child
            assert llmobs_service._instance._llmobs_eval_metric_writer != original_eval_metric_writer
            assert llmobs_service._instance._llmobs_eval_metric_writer.status == ServiceStatus.RUNNING
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


@pytest.mark.subprocess(
    env={
        "_DD_LLMOBS_WRITER_INTERVAL": "5.0",
        "PYTHONWARNINGS": "ignore::DeprecationWarning",
        # Force LLMOBS_AGENTLESS so finish enqueues into the writer directly; APM_AGENT would
        # cache for the rescue chain, leaving the buffer empty and defeating the assertions.
        "DD_APM_TRACING_ENABLED": "false",
        "DD_API_KEY": "<not-a-real-key>",
    }
)
def test_llmobs_fork_create_span():
    """Test that forking a process correctly encodes new spans created in each process."""
    import os

    import mock

    import ddtrace
    from ddtrace.llmobs import LLMObs as llmobs_service

    with mock.patch("ddtrace.llmobs._writer.BaseLLMObsWriter.periodic"):
        llmobs_service.enable(_tracer=ddtrace.tracer, ml_app="test_app")
        pid = os.fork()
        if pid:  # parent
            with llmobs_service.task():
                pass
            assert len(llmobs_service._instance._llmobs_span_writer._buffer) == 1
        else:  # child
            with llmobs_service.workflow():
                with llmobs_service.task():
                    pass
            assert len(llmobs_service._instance._llmobs_span_writer._buffer) == 2
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


@pytest.mark.subprocess(env={"PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_llmobs_fork_evaluator_runner_run():
    """Test that forking a process correctly encodes new spans created in each process."""
    import os
    import sys

    import mock

    import ddtrace
    from ddtrace.llmobs import LLMObs as llmobs_service

    try:
        import ragas  # noqa
    except ImportError:
        sys.exit(0)

    os.environ["DD_LLMOBS_EVALUATOR_INTERVAL"] = "5.0"
    os.environ["DD_LLMOBS_EVALUATORS"] = "ragas_faithfulness"
    os.environ.setdefault("OPENAI_API_KEY", "<not-a-real-key>")
    with mock.patch("ddtrace.llmobs._evaluators.runner.EvaluatorRunner.periodic"):
        llmobs_service.enable(_tracer=ddtrace.tracer, ml_app="test_app", api_key="test_api_key")
        pid = os.fork()
        if pid:  # parent
            llmobs_service._instance._evaluator_runner.enqueue({"span_id": "123", "trace_id": "456"}, None)
            assert len(llmobs_service._instance._evaluator_runner._buffer) == 1
        else:  # child
            llmobs_service._instance._evaluator_runner.enqueue({"span_id": "123", "trace_id": "456"}, None)
            assert len(llmobs_service._instance._evaluator_runner._buffer) == 1
            llmobs_service.disable()
            os._exit(12)

        _, status = os.waitpid(pid, 0)
        exit_code = os.WEXITSTATUS(status)
        assert exit_code == 12
        llmobs_service.disable()


@pytest.mark.subprocess(env={"DD_LLMOBS_ENABLED": "0", "PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_llmobs_fork_disabled():
    """Test that after being disabled the service remains disabled when forking"""
    import os

    import ddtrace
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.llmobs import LLMObs as llmobs_service

    svc = llmobs_service(tracer=ddtrace.tracer)
    pid = os.fork()
    assert not svc.enabled, "both the parent and child should be disabled"
    assert svc._llmobs_span_writer.status == ServiceStatus.STOPPED
    assert svc._llmobs_eval_metric_writer.status == ServiceStatus.STOPPED
    if not pid:
        svc.disable()
        os._exit(12)

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12
    svc.disable()


@pytest.mark.subprocess(env={"DD_LLMOBS_ENABLED": "0", "PYTHONWARNINGS": "ignore::DeprecationWarning"})
def test_llmobs_fork_disabled_then_enabled():
    """Test that after being initially disabled, the service can be enabled in a fork"""
    import os

    import ddtrace
    from ddtrace.internal.service import ServiceStatus
    from ddtrace.llmobs import LLMObs as llmobs_service
    from tests.utils import override_global_config

    svc = llmobs_service._instance
    pid = os.fork()
    assert not svc.enabled, "both the parent and child should be disabled"
    assert svc._llmobs_span_writer.status == ServiceStatus.STOPPED
    assert svc._llmobs_eval_metric_writer.status == ServiceStatus.STOPPED
    if not pid:
        # Enable the service in the child
        os.environ["DD_LLMOBS_ENABLED"] = "1"
        with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
            llmobs_service.enable(_tracer=ddtrace.tracer)
        svc = llmobs_service._instance
        assert svc._llmobs_span_writer.status == ServiceStatus.RUNNING
        assert svc._llmobs_eval_metric_writer.status == ServiceStatus.RUNNING
        svc.disable()
        os._exit(12)

    _, status = os.waitpid(pid, 0)
    exit_code = os.WEXITSTATUS(status)
    assert exit_code == 12
    svc.disable()


def test_llmobs_with_evaluator_runner(llmobs, mock_llmobs_evaluator_runner):
    with llmobs.llm(model_name="test_model"):
        pass
    time.sleep(0.1)
    assert llmobs._instance._evaluator_runner.enqueue.call_count == 1


def test_llmobs_with_evaluation_runner_does_not_enqueue_non_llm_spans(mock_llmobs_evaluator_runner, llmobs):
    with llmobs.workflow(name="test"):
        pass
    with llmobs.agent(name="test"):
        pass
    with llmobs.task(name="test"):
        pass
    with llmobs.embedding(model_name="test"):
        pass
    with llmobs.retrieval(name="test"):
        pass
    with llmobs.tool(name="test"):
        pass
    time.sleep(0.1)
    assert llmobs._instance._evaluator_runner.enqueue.call_count == 0


def test_annotation_context_modifies_span_tags(llmobs):
    with llmobs.annotation_context(tags={"foo": "bar"}):
        with llmobs.agent(name="test_agent") as span:
            assert {"foo": "bar"}.items() <= get_llmobs_tags(span).items()


def test_annotation_context_can_update_session_id(llmobs):
    with llmobs.annotation_context(tags={"session_id": "1234567890"}):
        with llmobs.agent(name="test_agent") as span:
            assert {"session_id": "1234567890"}.items() <= get_llmobs_tags(span).items()
            assert get_llmobs_session_id(span) == "1234567890"


def test_annotation_context_modifies_cost_tags(llmobs):
    with llmobs.annotation_context(tags={"team": "ml", "feature": "chatbot"}, cost_tags=["team", "feature"]):
        with llmobs.agent(name="test_agent") as span:
            assert {"team": "ml", "feature": "chatbot"}.items() <= get_llmobs_tags(span).items()
            assert get_llmobs_cost_tags(span) == ["team", "feature"]


def test_annotation_context_cost_tags_are_not_retained_for_tags_added_later(llmobs):
    with llmobs.annotation_context(cost_tags=["feature"]):
        with llmobs.agent(name="test_agent") as span:
            llmobs.annotate(span=span, tags={"feature": "chatbot"})
            assert {"feature": "chatbot"}.items() <= get_llmobs_tags(span).items()
            assert get_llmobs_cost_tags(span) is None


def test_annotation_context_modifies_prompt(llmobs):
    prompt = {"template": "test_template"}
    with llmobs.annotation_context(prompt=prompt):
        with llmobs.llm(name="test_agent", model_name="test") as span:
            assert get_llmobs_input_prompt(span) == {
                "id": "unnamed-ml-app_unnamed-prompt",
                "ml_app": "unnamed-ml-app",
                "template": "test_template",
                "_dd_context_variable_keys": ["context"],
                "_dd_query_variable_keys": ["question"],
            }
            assert {PROMPT_TRACKING_INSTRUMENTATION_METHOD: "annotated"}.items() <= get_llmobs_tags(span).items()


def test_annotation_context_prompt_includes_ml_app(llmobs):
    prompt = {"template": "test_template"}
    with llmobs.annotation_context(prompt=prompt):
        with llmobs.llm(name="test_agent", model_name="test") as span:
            assert (get_llmobs_input_prompt(span) or {}).get("ml_app") == "unnamed-ml-app"


def test_annotation_context_modifies_name(llmobs):
    with llmobs.annotation_context(name="test_agent_override"):
        with llmobs.llm(name="test_agent", model_name="test") as span:
            assert span.name == "test_agent_override"


def test_annotation_context_modifies_span_links(llmobs):
    with llmobs.annotation_context(_linked_spans=[{"span_id": "123", "trace_id": "456"}]):
        with llmobs.llm(model_name="test_model") as span:
            llmobs.annotate(span=span, _linked_spans=[{"span_id": "abc", "trace_id": "def"}])
            assert get_llmobs_span_links(span) == [
                {"span_id": "123", "trace_id": "456", "attributes": {"from": "output", "to": "input"}},
                {"span_id": "abc", "trace_id": "def", "attributes": {"from": "output", "to": "input"}},
            ]


def test_annotation_context_finished_context_does_not_modify_tags(llmobs):
    with llmobs.annotation_context(tags={"foo": "bar"}):
        pass
    with llmobs.agent(name="test_agent") as span:
        assert "foo" not in get_llmobs_tags(span)


def test_annotation_context_finished_context_does_not_modify_prompt(llmobs):
    with llmobs.annotation_context(prompt={"template": "test_template"}):
        pass
    with llmobs.llm(name="test_agent", model_name="test") as span:
        assert get_llmobs_input_prompt(span) is None


def test_annotation_context_finished_context_does_not_modify_name(llmobs):
    with llmobs.annotation_context(name="test_agent_override"):
        pass
    with llmobs.agent(name="test_agent") as span:
        assert span.name == "test_agent"


def test_agent_span_sets_agent_version_tag(llmobs):
    with llmobs.agent(name="test_agent", version="v3") as span:
        pass
    assert get_llmobs_tags(span)["agent_version"] == "v3"


def test_agent_span_version_not_set_on_children(llmobs):
    """The version identifies the agent, so it stays on the agent span."""
    with llmobs.agent(name="test_agent", version="v3"):
        with llmobs.workflow(name="test_workflow") as workflow_span:
            with llmobs.llm(name="test_llm", model_name="test") as llm_span:
                pass
            with llmobs.tool(name="test_tool") as tool_span:
                pass
    for span in (workflow_span, llm_span, tool_span):
        assert "agent_version" not in get_llmobs_tags(span)


def test_agent_span_without_version_sets_no_tag(llmobs):
    with llmobs.agent(name="test_agent") as span:
        pass
    assert "agent_version" not in get_llmobs_tags(span)


def test_nested_agent_spans_each_carry_their_own_version(llmobs):
    with llmobs.agent(name="outer_agent", version="v1") as outer_span:
        with llmobs.agent(name="inner_agent", version="v3") as inner_span:
            pass
    assert get_llmobs_tags(outer_span)["agent_version"] == "v1"
    assert get_llmobs_tags(inner_span)["agent_version"] == "v3"


def test_nested_agent_span_does_not_inherit_ancestor_version(llmobs):
    """An unversioned sub-agent stays unversioned rather than claiming its parent's version."""
    with llmobs.agent(name="outer_agent", version="v1"):
        with llmobs.agent(name="inner_agent") as inner_span:
            pass
    assert "agent_version" not in get_llmobs_tags(inner_span)


def test_annotation_context_sets_agent_tags_on_agent_span_only(llmobs):
    with llmobs.annotation_context(agent={"version": "v3"}):
        with llmobs.agent(name="test_agent") as agent_span:
            with llmobs.llm(name="test_llm", model_name="test") as llm_span:
                pass
    assert get_llmobs_tags(agent_span)["agent_version"] == "v3"
    assert "agent_version" not in get_llmobs_tags(llm_span)


def test_user_supplied_agent_version_tag_is_left_alone(llmobs):
    """`tags` is an arbitrary user namespace, so an app already using this key keeps it."""
    with llmobs.annotation_context(tags={"agent_version": "mine-v1"}):
        with llmobs.workflow(name="test_workflow") as workflow_span:
            pass
        with llmobs.agent(name="test_agent") as agent_span:
            pass
    for span in (workflow_span, agent_span):
        assert get_llmobs_tags(span)["agent_version"] == "mine-v1"


def test_annotation_context_agent_version_wins_over_explicit_tag(llmobs):
    with llmobs.annotation_context(tags={"agent_version": "from_tags"}, agent={"version": "from_agent"}):
        with llmobs.agent(name="test_agent") as span:
            pass
    assert get_llmobs_tags(span)["agent_version"] == "from_agent"


@pytest.mark.parametrize("agent", [{}, {"version": None}, {"version": ""}, {"name": "no-version"}, "not_a_dict", 42])
def test_annotation_context_agent_without_version_sets_no_tag(llmobs, agent):
    """Agent input is unvalidated by design: anything without a usable version is a no-op."""
    with llmobs.annotation_context(agent=agent):
        with llmobs.agent(name="test_agent") as span:
            pass
    assert "agent_version" not in get_llmobs_tags(span)


def test_annotation_context_finished_context_does_not_modify_agent_version(llmobs):
    with llmobs.annotation_context(agent={"version": "v3"}):
        pass
    with llmobs.agent(name="test_agent") as span:
        pass
    assert "agent_version" not in get_llmobs_tags(span)


def test_annotation_context_nested_overrides_agent_version(llmobs):
    with llmobs.annotation_context(agent={"version": "outer"}):
        with llmobs.annotation_context(agent={"version": "inner"}):
            with llmobs.agent(name="test_agent") as span:
                pass
    assert get_llmobs_tags(span)["agent_version"] == "inner"


def test_annotate_sets_agent_version_tag(llmobs):
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(span=span, agent={"version": "v3"})
    assert get_llmobs_tags(span)["agent_version"] == "v3"


def test_annotation_context_nested(llmobs):
    with llmobs.annotation_context(tags={"foo": "bar", "boo": "bar"}):
        with llmobs.annotation_context(tags={"foo": "baz"}):
            with llmobs.agent(name="test_agent") as span:
                assert {"foo": "baz", "boo": "bar"}.items() <= get_llmobs_tags(span).items()


def test_annotation_context_nested_overrides_name(llmobs):
    with llmobs.annotation_context(name="unexpected"):
        with llmobs.annotation_context(name="expected"):
            with llmobs.agent(name="test_agent") as span:
                assert span.name == "expected"


def test_annotation_context_nested_maintains_trace_structure(llmobs):
    """This test makes sure starting/stopping annotation contexts do not modify the llmobs trace structure"""
    with llmobs.annotation_context(tags={"foo": "bar", "boo": "bar"}):
        with llmobs.agent(name="parent_span") as parent_span:
            with llmobs.annotation_context(tags={"foo": "baz"}):
                with llmobs.workflow(name="child_span") as child_span:
                    assert {"foo": "baz", "boo": "bar"}.items() <= get_llmobs_tags(child_span).items()
                    assert {"foo": "bar", "boo": "bar"}.items() <= get_llmobs_tags(parent_span).items()

    assert get_llmobs_trace_id(child_span) == get_llmobs_trace_id(parent_span)
    assert child_span.span_id != parent_span.span_id
    assert get_llmobs_parent_id(child_span) == str(parent_span.span_id)
    assert get_llmobs_parent_id(parent_span) == "undefined"
    parent_apm_trace_id = format_trace_id(parent_span.trace_id)
    child_apm_trace_id = format_trace_id(child_span.trace_id)
    assert child_apm_trace_id == parent_apm_trace_id
    assert parent_apm_trace_id != get_llmobs_trace_id(parent_span)


def test_annotation_context_separate_traces_maintained(llmobs):
    with llmobs.annotation_context(tags={"foo": "bar", "boo": "bar"}):
        with llmobs.agent(name="parent_span") as agent_span:
            pass
        with llmobs.workflow(name="child_span") as workflow_span:
            pass

    assert get_llmobs_trace_id(agent_span) != get_llmobs_trace_id(workflow_span)
    assert agent_span.span_id != workflow_span.span_id
    assert get_llmobs_parent_id(workflow_span) == "undefined"
    assert get_llmobs_parent_id(agent_span) == "undefined"


def test_annotation_context_persists_across_multiple_root_span_operations(llmobs):
    """
    Regression test: verifies that annotation context tags persist across multiple
    sequential root span operations. This simulates scenarios like multiple batch()
    calls with structured outputs in Langchain, where each batch creates a root span
    that finishes before the next batch starts.

    The bug occurred because the trace context wasn't being reactivated after a root
    span finished, causing subsequent operations to lose the annotation context's baggage.
    """
    with llmobs.annotation_context(tags={"test_tag": "should_persist"}):
        # First operation - creates and finishes a root span
        with llmobs.workflow(name="first_batch") as span1:
            assert {"test_tag": "should_persist"}.items() <= get_llmobs_tags(span1).items()

        # Second operation - should still have annotation context applied
        with llmobs.workflow(name="second_batch") as span2:
            assert {"test_tag": "should_persist"}.items() <= get_llmobs_tags(span2).items()

        # Third operation - verify it continues to work
        with llmobs.agent(name="third_operation") as span3:
            assert {"test_tag": "should_persist"}.items() <= get_llmobs_tags(span3).items()


def test_annotation_context_not_reactivated_after_exit(llmobs):
    """
    Verifies that once an annotation context exits, the context we created is not
    reactivated even after subsequent span operations within a new context.
    """
    with llmobs.annotation_context(tags={"inside": "context"}):
        with llmobs.workflow(name="inside_span") as span1:
            assert {"inside": "context"}.items() <= get_llmobs_tags(span1).items()

    # After exiting annotation_context, tags should not be applied
    with llmobs.workflow(name="outside_span") as span2:
        assert "inside" not in get_llmobs_tags(span2)


def test_annotation_context_sequential_contexts_work_independently(llmobs):
    """
    Regression test: Verifies that multiple sequential annotation_contexts work correctly.
    This tests the specific customer issue where using annotation_context multiple times
    (e.g., with LangChain's structured outputs and batch()) would cause the second context's
    annotations to fail after the first batch call.

    The bug occurred because:
    1. First annotation_context creates a Context with ANNOTATIONS_CONTEXT_ID=X
    2. First annotation_context exits, but the Context remains active (with _reactivate=False)
    3. Second annotation_context enters and reuses the stale Context's ANNOTATIONS_CONTEXT_ID=X
    4. After first span finishes in second context, the Context is not reactivated
    5. Subsequent spans don't have ANNOTATIONS_CONTEXT_ID, so annotations fail
    """
    # First annotation context
    with llmobs.annotation_context(tags={"context": "first"}):
        with llmobs.workflow(name="first_ctx_op1") as span1:
            assert {"context": "first"}.items() <= get_llmobs_tags(span1).items()
        with llmobs.workflow(name="first_ctx_op2") as span2:
            assert {"context": "first"}.items() <= get_llmobs_tags(span2).items()

    # Second annotation context - this is where the bug manifested
    with llmobs.annotation_context(tags={"context": "second"}):
        # First operation works (reused old context ID)
        with llmobs.workflow(name="second_ctx_op1") as span3:
            assert {"context": "second"}.items() <= get_llmobs_tags(span3).items()
        # Second operation failed before the fix (context not reactivated)
        with llmobs.workflow(name="second_ctx_op2") as span4:
            assert {"context": "second"}.items() <= get_llmobs_tags(span4).items()
        # Third operation to verify it continues to work
        with llmobs.agent(name="second_ctx_op3") as span5:
            assert {"context": "second"}.items() <= get_llmobs_tags(span5).items()

    # Third annotation context - verify it still works
    with llmobs.annotation_context(tags={"context": "third"}):
        with llmobs.workflow(name="third_ctx_op1") as span6:
            assert {"context": "third"}.items() <= get_llmobs_tags(span6).items()
        with llmobs.workflow(name="third_ctx_op2") as span7:
            assert {"context": "third"}.items() <= get_llmobs_tags(span7).items()


def test_annotation_context_only_applies_to_local_context(llmobs):
    """
    tests that annotation contexts only apply to spans belonging to the same
    trace context and not globally to all spans.
    """
    agent_has_correct_name = False
    agent_has_correct_tags = False
    tool_has_correct_name = False
    tool_does_not_have_tags = False

    event = threading.Event()

    # thread which registers an annotation context for 0.1 seconds
    def context_one():
        nonlocal agent_has_correct_name
        nonlocal agent_has_correct_tags
        with llmobs.annotation_context(name="expected_agent", tags={"foo": "bar"}):
            with llmobs.agent(name="test_agent") as span:
                event.wait()
                agent_has_correct_tags = {"foo": "bar"}.items() <= get_llmobs_tags(span).items()
                agent_has_correct_name = span.name == "expected_agent"

    # thread which registers an annotation context for 0.5 seconds
    def context_two():
        nonlocal tool_has_correct_name
        nonlocal tool_does_not_have_tags
        with llmobs.agent(name="test_agent"):
            with llmobs.annotation_context(name="expected_tool"):
                with llmobs.tool(name="test_tool") as tool_span:
                    event.wait()
                    tool_does_not_have_tags = "foo" not in get_llmobs_tags(tool_span)
                    tool_has_correct_name = tool_span.name == "expected_tool"

    thread_one = threading.Thread(target=context_one)
    thread_two = threading.Thread(target=context_two)
    thread_one.start()
    thread_two.start()

    with llmobs.agent(name="test_agent") as span:
        assert span.name == "test_agent"
        assert "foo" not in get_llmobs_tags(span)

    event.set()
    thread_one.join()
    thread_two.join()

    # the context's in each thread shouldn't alter the span name of
    # spans started in other threads.
    assert agent_has_correct_name is True
    assert tool_has_correct_name is True
    assert agent_has_correct_tags is True
    assert tool_does_not_have_tags is True


async def test_annotation_context_async_modifies_span_tags(llmobs):
    async with llmobs.annotation_context(tags={"foo": "bar"}):
        with llmobs.agent(name="test_agent") as span:
            assert {"foo": "bar"}.items() <= get_llmobs_tags(span).items()


async def test_annotation_context_async_modifies_cost_tags(llmobs):
    async with llmobs.annotation_context(tags={"team": "ml", "feature": "chatbot"}, cost_tags=["team", "feature"]):
        with llmobs.agent(name="test_agent") as span:
            assert {"team": "ml", "feature": "chatbot"}.items() <= get_llmobs_tags(span).items()
            assert get_llmobs_cost_tags(span) == ["team", "feature"]


async def test_annotation_context_async_modifies_prompt(llmobs):
    prompt = {"template": "test_template"}
    async with llmobs.annotation_context(prompt=prompt):
        with llmobs.llm(name="test_agent", model_name="test") as span:
            assert get_llmobs_input_prompt(span) == {
                "id": "unnamed-ml-app_unnamed-prompt",
                "ml_app": "unnamed-ml-app",
                "template": "test_template",
                "_dd_context_variable_keys": ["context"],
                "_dd_query_variable_keys": ["question"],
            }
            assert {PROMPT_TRACKING_INSTRUMENTATION_METHOD: "annotated"}.items() <= get_llmobs_tags(span).items()


async def test_annotation_context_async_modifies_name(llmobs):
    async with llmobs.annotation_context(name="test_agent_override"):
        with llmobs.llm(name="test_agent", model_name="test") as span:
            assert span.name == "test_agent_override"


async def test_annotation_context_async_finished_context_does_not_modify_tags(llmobs):
    async with llmobs.annotation_context(tags={"foo": "bar"}):
        pass
    with llmobs.agent(name="test_agent") as span:
        assert "foo" not in get_llmobs_tags(span)


async def test_annotation_context_async_finished_context_does_not_modify_prompt(llmobs):
    async with llmobs.annotation_context(prompt={"template": "test_template"}):
        pass
    with llmobs.llm(name="test_agent", model_name="test") as span:
        assert get_llmobs_input_prompt(span) is None


async def test_annotation_context_finished_context_async_does_not_modify_name(llmobs):
    async with llmobs.annotation_context(name="test_agent_override"):
        pass
    with llmobs.agent(name="test_agent") as span:
        assert span.name == "test_agent"


async def test_annotation_context_async_nested(llmobs):
    async with llmobs.annotation_context(tags={"foo": "bar", "boo": "bar"}):
        async with llmobs.annotation_context(tags={"foo": "baz"}):
            with llmobs.agent(name="test_agent") as span:
                assert {"foo": "baz", "boo": "bar"}.items() <= get_llmobs_tags(span).items()


def test_service_enable_starts_evaluator_runner_when_evaluators_exist(tracer):
    pytest.importorskip("ragas")
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        with override_env(dict(DD_LLMOBS_EVALUATORS="ragas_faithfulness")):
            llmobs_service.enable(_tracer=tracer)
            llmobs_instance = llmobs_service._instance
            assert llmobs_instance is not None
            assert llmobs_service.enabled
            assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "running"
            assert llmobs_service._instance._evaluator_runner.status.value == "running"
            llmobs_service.disable()


def test_service_enable_does_not_start_evaluator_runner(tracer):
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>", _llmobs_ml_app="<ml-app-name>")):
        llmobs_service.enable(_tracer=tracer)
        llmobs_instance = llmobs_service._instance
        assert llmobs_instance is not None
        assert llmobs_service.enabled
        assert llmobs_service._instance._llmobs_eval_metric_writer.status.value == "running"
        assert llmobs_service._instance._llmobs_span_writer.status.value == "running"
        assert llmobs_service._instance._evaluator_runner.status.value == "stopped"
        llmobs_service.disable()


def test_export_span_when_llmobs_is_disabled_returns_none(llmobs):
    llmobs.disable()
    assert llmobs.export_span() is None


def test_submit_evaluation_span_incorrect_type_raises(llmobs):
    with pytest.raises(
        TypeError,
        match=re.escape(
            (
                "`span` must be a dictionary containing both span_id and trace_id keys. "
                "LLMObs.export_span() can be used to generate this dictionary from a given span."
            )
        ),
    ):
        llmobs.submit_evaluation(span="asd", label="toxicity", metric_type="categorical", value="high")


def test_submit_evaluation_span_with_tag_value_incorrect_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(
        TypeError,
        match=r"`span_with_tag_value` must be a dict with keys 'tag_key' and 'tag_value' containing string values",
    ):
        llmobs.submit_evaluation(span_with_tag_value="asd", label="toxicity", metric_type="categorical", value="high")
    with pytest.raises(
        TypeError,
        match=r"`span_with_tag_value` must be a dict with keys 'tag_key' and 'tag_value' containing string values",
    ):
        llmobs.submit_evaluation(
            span_with_tag_value={"tag_key": "hi", "tag_value": 1},
            label="toxicity",
            metric_type="categorical",
            value="high",
        )


def test_submit_evaluation_empty_span_or_trace_id_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(
        TypeError,
        match=re.escape(
            (
                "`span` must be a dictionary containing both span_id and trace_id keys. "
                "LLMObs.export_span() can be used to generate this dictionary from a given span."
            )
        ),
    ):
        llmobs.submit_evaluation(span={"trace_id": "456"}, label="toxicity", metric_type="categorical", value="high")
    with pytest.raises(
        TypeError,
        match=re.escape(
            "`span` must be a dictionary containing both span_id and trace_id keys. "
            "LLMObs.export_span() can be used to generate this dictionary from a given span."
        ),
    ):
        llmobs.submit_evaluation(span={"span_id": "456"}, label="toxicity", metric_type="categorical", value="high")


def test_submit_evaluation_span_with_tag_value_empty_key_or_val_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(
        TypeError,
        match=r"`span_with_tag_value` must be a dict with keys 'tag_key' and 'tag_value' containing string values",
    ):
        llmobs.submit_evaluation(
            span_with_tag_value={"tag_value": "123"}, label="toxicity", metric_type="categorical", value="high"
        )


def test_submit_evaluation_invalid_timestamp_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(
        ValueError, match="timestamp_ms must be a non-negative integer. Evaluation metric data will not be sent"
    ):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"},
            label="",
            metric_type="categorical",
            value="high",
            ml_app="dummy",
            timestamp_ms="invalid",
        )


def test_submit_evaluation_empty_label_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(ValueError, match="label must be the specified name of the evaluation metric."):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"}, label="", metric_type="categorical", value="high"
        )


def test_submit_evaluation_label_value_with_a_period_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(ValueError, match="label value must not contain a '.'."):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"}, label="toxicity.0", metric_type="categorical", value="high"
        )


def test_submit_evaluation_incorrect_metric_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(ValueError, match="metric_type must be one of 'categorical', 'score', 'boolean', or 'json'."):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="wrong", value="high"
        )
    with pytest.raises(ValueError, match="metric_type must be one of 'categorical', 'score', 'boolean', or 'json'."):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="", value="high"
        )


def test_submit_evaluation_incorrect_score_value_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(TypeError, match="value must be an integer or float for a score metric."):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"}, label="token_count", metric_type="score", value="high"
        )


def test_submit_evaluation_validation_error_telemetry(llmobs, mock_llmobs_eval_metric_writer):
    with mock.patch("ddtrace.llmobs._llmobs.telemetry.record_llmobs_submit_evaluation") as record_telemetry:
        with pytest.raises(TypeError, match="value must be an integer or float for a score metric."):
            llmobs.submit_evaluation(
                span={"span_id": "123", "trace_id": "456"},
                label="token_count",
                metric_type="score",
                value="high",
            )

    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()
    record_telemetry.assert_called_once_with(
        {"span": {"span_id": "123", "trace_id": "456"}},
        "score",
        "invalid_metric_value",
    )


def test_submit_evaluation_invalid_tags_raises(llmobs):
    with pytest.raises(Exception) as excinfo:
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="high",
            tags=["invalid"],
        )
    assert str(excinfo.value) == "tags must be a dictionary of string key-value pairs."


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(_llmobs_ml_app="test_app_name")],
)
def test_submit_evaluation_non_string_tags_raises(llmobs):  # TODO(sabrenner): check if we're ok changing this behavior
    with pytest.raises(Exception) as excinfo:
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="high",
            tags={1: 2, "foo": "bar"},
            ml_app="dummy",
        )
    assert str(excinfo.value) == "Failed to parse tags. Tags for evaluation metrics must be strings."


@pytest.mark.parametrize(
    "ddtrace_global_config",
    [dict(ddtrace="1.2.3", env="test_env", service="test_service", _llmobs_ml_app="test_app_name")],
)
def test_submit_evaluation_metric_tags(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="ml_app_override",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
            tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:ml_app_override", "foo:bar", "bee:baz"],
        )
    )


def test_submit_evaluation_agent_service_tags(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar"},
        ml_app="legacy_ml_app",
        agent_service="agent_service",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="agent_service",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
            tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:agent_service", "foo:bar"],
        )
    )


def test_submit_evaluation_span_with_tag_value_enqueues_writer_with_categorical_metric(
    llmobs, mock_llmobs_eval_metric_writer
):
    llmobs.submit_evaluation(
        span_with_tag_value={"tag_key": "tag_key", "tag_value": "tag_val"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        ml_app="dummy",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            tag_key="tag_key",
            tag_value="tag_val",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
        )
    )


def test_submit_evaluation_enqueues_writer_with_categorical_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        ml_app="dummy",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="dummy",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
        )
    )
    mock_llmobs_eval_metric_writer.reset_mock()
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.submit_evaluation(
            span=llmobs.export_span(span),
            label="toxicity",
            metric_type="categorical",
            value="high",
            ml_app="dummy",
        )
        mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
            _expected_llmobs_eval_metric_event(
                ml_app="dummy",
                span_id=str(span.span_id),
                trace_id=get_llmobs_trace_id(span),
                label="toxicity",
                metric_type="categorical",
                categorical_value="high",
            )
        )


def test_submit_evaluation_enqueues_writer_with_score_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="sentiment",
        metric_type="score",
        value=0.9,
        ml_app="dummy",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            span_id="123", trace_id="456", label="sentiment", metric_type="score", score_value=0.9, ml_app="dummy"
        )
    )
    mock_llmobs_eval_metric_writer.reset_mock()
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.submit_evaluation(
            span=llmobs.export_span(span), label="sentiment", metric_type="score", value=0.9, ml_app="dummy"
        )
        mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
            _expected_llmobs_eval_metric_event(
                span_id=str(span.span_id),
                trace_id=get_llmobs_trace_id(span),
                label="sentiment",
                metric_type="score",
                score_value=0.9,
                ml_app="dummy",
            )
        )


def test_submit_evaluation_metric_with_metadata_enqueues_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
        metadata={"foo": ["bar", "baz"]},
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="ml_app_override",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
            tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:ml_app_override", "foo:bar", "bee:baz"],
            metadata={"foo": ["bar", "baz"]},
        )
    )


def test_submit_evaluation_invalid_assessment_raises(llmobs):
    with pytest.raises(Exception) as excinfo:
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="high",
            assessment=True,
        )
    assert str(excinfo.value) == "Failed to parse assessment. assessment must be either 'pass' or 'fail'."


def test_submit_evaluation_enqueues_writer_with_assessment(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
        metadata={"foo": ["bar", "baz"]},
        assessment="fail",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="ml_app_override",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
            tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:ml_app_override", "foo:bar", "bee:baz"],
            metadata={"foo": ["bar", "baz"]},
            assessment="fail",
        )
    )
    mock_llmobs_eval_metric_writer.reset()
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
        metadata={"foo": ["bar", "baz"]},
        assessment="fail",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="ml_app_override",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
            tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:ml_app_override", "foo:bar", "bee:baz"],
            metadata={"foo": ["bar", "baz"]},
            assessment="fail",
        )
    )


def test_submit_evaluation_invalid_reasoning_raises(llmobs):
    with pytest.raises(Exception) as excinfo:
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="high",
            reasoning=123,
        )
    assert str(excinfo.value) == "Failed to parse reasoning. reasoning must be a string."


def test_submit_evaluation_enqueues_writer_with_reasoning(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="toxicity",
        metric_type="categorical",
        value="high",
        tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
        ml_app="ml_app_override",
        metadata={"foo": ["bar", "baz"]},
        reasoning="the content of the message involved profanity",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            ml_app="ml_app_override",
            span_id="123",
            trace_id="456",
            label="toxicity",
            metric_type="categorical",
            categorical_value="high",
            tags=["ddtrace.version:{}".format(ddtrace.__version__), "ml_app:ml_app_override", "foo:bar", "bee:baz"],
            metadata={"foo": ["bar", "baz"]},
            reasoning="the content of the message involved profanity",
        )
    )
    mock_llmobs_eval_metric_writer.reset_mock()
    with pytest.raises(Exception) as excinfo:
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"},
            label="toxicity",
            metric_type="categorical",
            value="low",
            tags={"foo": "bar", "bee": "baz", "ml_app": "ml_app_override"},
            ml_app="ml_app_override",
            metadata="invalid",
            reasoning="the content of the message did not involve profanity or hate speech or negativity",
        )
    assert str(excinfo.value) == "metadata must be json serializable dictionary."
    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()


def test_llmobs_parenting_with_root_apm_span(llmobs, tracer):
    # orphaned llmobs spans with apm root have undefined parent_id
    with tracer.trace("no_llm_span"):
        with llmobs.task("llm_span") as llm_span:
            pass
        with llmobs.task("llm_span_2") as llm_span_2:
            pass
    assert get_llmobs_span_name(llm_span) == "llm_span"
    assert get_llmobs_parent_id(llm_span) == "undefined"
    assert get_llmobs_span_name(llm_span_2) == "llm_span_2"
    assert get_llmobs_parent_id(llm_span_2) == "undefined"
    # document buggy `trace_id` behavior
    assert format_trace_id(llm_span.trace_id) == format_trace_id(llm_span_2.trace_id)
    assert get_llmobs_trace_id(llm_span) != get_llmobs_trace_id(llm_span_2)


def test_llmobs_parenting_with_intermixed_apm_spans(llmobs, tracer):
    with llmobs.task("level_1_llm") as level_1_span:
        with tracer.trace("intermediate_apm"):  # APM span
            with tracer.trace("intermediate_apm_2"):  # APM span
                with llmobs.task("level_2_llm_a") as level_2_a_span:
                    with tracer.trace("intermediate_apm_3"):  # APM span
                        with llmobs.task("level_3_llm") as level_3_span:
                            pass
                with llmobs.task("level_2_llm_b") as level_2_b_span:
                    pass
    """
    Expected LLM Obs trace structure;
        level_1_llm
            level_2_llm_a
                level_3_llm
            level_2_llm_b
    """
    assert get_llmobs_span_name(level_3_span) == "level_3_llm"
    assert get_llmobs_parent_id(level_3_span) == str(level_2_a_span.span_id)

    assert get_llmobs_span_name(level_2_a_span) == "level_2_llm_a"
    assert get_llmobs_parent_id(level_2_a_span) == str(level_1_span.span_id)

    assert get_llmobs_span_name(level_2_b_span) == "level_2_llm_b"
    assert get_llmobs_parent_id(level_2_b_span) == str(level_1_span.span_id)

    assert get_llmobs_span_name(level_1_span) == "level_1_llm"
    assert get_llmobs_parent_id(level_1_span) == "undefined"

    level_3_apm_trace_id = format_trace_id(level_3_span.trace_id)
    level_3_trace_id = get_llmobs_trace_id(level_3_span)
    assert level_3_apm_trace_id != level_3_trace_id
    for span in (level_1_span, level_2_a_span, level_2_b_span, level_3_span):
        assert get_llmobs_trace_id(span) == level_3_trace_id
        assert format_trace_id(span.trace_id) == level_3_apm_trace_id


def test_submit_evaluation_enqueues_writer_with_boolean_metric(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="is_toxic",
        metric_type="boolean",
        value=True,
        ml_app="dummy",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
        _expected_llmobs_eval_metric_event(
            span_id="123", trace_id="456", label="is_toxic", metric_type="boolean", boolean_value=True, ml_app="dummy"
        )
    )
    mock_llmobs_eval_metric_writer.reset_mock()
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        llmobs.submit_evaluation(
            span=llmobs.export_span(span),
            label="is_toxic",
            metric_type="boolean",
            value=False,
            ml_app="dummy",
        )
        mock_llmobs_eval_metric_writer.enqueue.assert_called_with(
            _expected_llmobs_eval_metric_event(
                span_id=str(span.span_id),
                trace_id=get_llmobs_trace_id(span),
                label="is_toxic",
                metric_type="boolean",
                boolean_value=False,
                ml_app="dummy",
            )
        )


def test_submit_evaluation_incorrect_boolean_value_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(TypeError, match="value must be a boolean for a boolean metric."):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"}, label="is_toxic", metric_type="boolean", value="true"
        )


def test_submit_evaluation_incorrect_categorical_value_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(TypeError, match="value must be a string for a categorical metric."):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="categorical", value=123
        )


def test_submit_evaluation_incorrect_json_value_type_raises_error(llmobs, mock_llmobs_logs):
    with pytest.raises(TypeError, match="value must be a dict for a json metric."):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"}, label="toxicity", metric_type="json", value="high"
        )


def test_submit_evaluation_invalid_eval_scope_raises_error(llmobs):
    with pytest.raises(ValueError, match="eval_scope must be one of 'span' or 'trace'."):
        llmobs.submit_evaluation(
            span={"span_id": "123", "trace_id": "456"},
            label="quality",
            metric_type="score",
            value=0.9,
            eval_scope="invalid",
        )


def test_submit_evaluation_trace_scope(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_evaluation(
        span={"span_id": "123", "trace_id": "456"},
        label="quality",
        metric_type="score",
        value=0.9,
        ml_app="test_app",
        eval_scope="trace",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        {
            "event_kind": "evaluation",
            "metric_type": "score",
            "label": "quality",
            "tags": [
                "ddtrace.version:{}".format(ddtrace.__version__),
                "ml_app:test_app",
            ],
            "join_on": {"span": {"span_id": "123", "trace_id": "456"}},
            "score_value": 0.9,
            "timestamp_ms": mock.ANY,
            "ml_app": "test_app",
            "eval_scope": "trace",
        }
    )


@pytest.mark.parametrize(
    ("target_kwargs", "target_type", "target_value"),
    [
        pytest.param(
            {"span": {"span_id": "span-1", "trace_id": "ignored-trace", "is_otel": True}},
            "span_id",
            "span-1",
            id="span",
        ),
        pytest.param({"span_id": "span-2"}, "span_id", "span-2", id="span-id"),
        pytest.param({"trace_id": "trace-1"}, "trace_id", "trace-1", id="trace-id"),
        pytest.param({"session_id": "session-1"}, "session_id", "session-1", id="session-id"),
        pytest.param(
            {"feedback_join_key": "order-123"},
            "feedback_join_key",
            "order-123",
            id="feedback-join-key",
        ),
    ],
)
def test_submit_feedback_enqueues_exact_target_payload(
    llmobs,
    mock_llmobs_eval_metric_writer,
    target_kwargs,
    target_type,
    target_value,
):
    with mock.patch("ddtrace.llmobs._llmobs.telemetry.record_llmobs_submit_feedback") as record_telemetry:
        llmobs.submit_feedback(
            label="helpfulness",
            metric_type="categorical",
            value="helpful",
            submitter={"id": "user-1", "type": "reviewer", "ignored": "not-serialized"},
            ml_app="feedback-app",
            **target_kwargs,
        )

    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _expected_llmobs_feedback_event(
            metric_type="categorical",
            label="helpfulness",
            value="helpful",
            submitter={"id": "user-1", "type": "reviewer"},
            target_type=target_type,
            target_value=target_value,
            ml_app="feedback-app",
        )
    )
    record_telemetry.assert_called_once_with(target_type, "categorical", None)


def test_submit_feedback_with_exported_span_only_emits_span_id(llmobs, mock_llmobs_eval_metric_writer):
    with llmobs.llm(model_name="test_model", name="test_llm_call", model_provider="test_provider") as span:
        exported_span = llmobs.export_span(span)
        assert exported_span is not None
        assert exported_span["trace_id"] == get_llmobs_trace_id(span)
        llmobs.submit_feedback(
            span=exported_span,
            label="comment",
            metric_type="text",
            value="This answer was useful.",
            submitter={"id": "user-1"},
            ml_app="feedback-app",
        )

    expected_event = _expected_llmobs_feedback_event(
        metric_type="text",
        label="comment",
        value="This answer was useful.",
        submitter={"id": "user-1"},
        target_type="span_id",
        target_value=str(span.span_id),
        ml_app="feedback-app",
    )
    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(expected_event)
    feedback_event = mock_llmobs_eval_metric_writer.enqueue.call_args.args[0]
    assert "trace_id" not in feedback_event
    assert "join_on" not in feedback_event
    assert "eval_scope" not in feedback_event
    assert "metadata" not in feedback_event


@pytest.mark.parametrize(
    "target_kwargs",
    [
        pytest.param({}, id="no-target"),
        pytest.param({"span_id": "span-1", "trace_id": "trace-1"}, id="multiple-targets"),
    ],
)
def test_submit_feedback_requires_exactly_one_target(llmobs, mock_llmobs_eval_metric_writer, target_kwargs):
    with mock.patch("ddtrace.llmobs._llmobs.telemetry.record_llmobs_submit_feedback") as record_telemetry:
        with pytest.raises(ValueError, match="Exactly one of"):
            llmobs.submit_feedback(
                label="helpfulness",
                metric_type="categorical",
                value="helpful",
                submitter={"id": "user-1"},
                **target_kwargs,
            )

    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()
    record_telemetry.assert_called_once_with("other", "categorical", "invalid_target_count")


@pytest.mark.parametrize("target_type", ["span_id", "trace_id", "session_id", "feedback_join_key"])
@pytest.mark.parametrize(
    ("target_value", "exception_type"),
    [
        pytest.param("", ValueError, id="empty"),
        pytest.param(123, TypeError, id="not-a-string"),
    ],
)
def test_submit_feedback_rejects_invalid_direct_identifier(
    llmobs,
    mock_llmobs_eval_metric_writer,
    target_type,
    target_value,
    exception_type,
):
    with mock.patch("ddtrace.llmobs._llmobs.telemetry.record_llmobs_submit_feedback") as record_telemetry:
        with pytest.raises(exception_type, match=r"must be a non-empty string"):
            llmobs.submit_feedback(
                label="helpfulness",
                metric_type="categorical",
                value="helpful",
                submitter={"id": "user-1"},
                **{target_type: target_value},
            )

    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()
    record_telemetry.assert_called_once_with(
        target_type,
        "categorical",
        "invalid_{}".format(target_type),
    )


@pytest.mark.parametrize(
    ("span", "exception_type", "message"),
    [
        pytest.param("not-a-span", TypeError, "dictionary containing a string span_id", id="not-a-dictionary"),
        pytest.param({"trace_id": "trace-1"}, TypeError, "dictionary containing a string span_id", id="missing-id"),
        pytest.param({"span_id": 123}, TypeError, "dictionary containing a string span_id", id="non-string-id"),
        pytest.param({"span_id": ""}, ValueError, "non-empty string span_id", id="empty-id"),
    ],
)
def test_submit_feedback_rejects_invalid_exported_span(
    llmobs,
    mock_llmobs_eval_metric_writer,
    span,
    exception_type,
    message,
):
    with mock.patch("ddtrace.llmobs._llmobs.telemetry.record_llmobs_submit_feedback") as record_telemetry:
        with pytest.raises(exception_type, match=message):
            llmobs.submit_feedback(
                span=span,
                label="helpfulness",
                metric_type="categorical",
                value="helpful",
                submitter={"id": "user-1"},
            )

    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()
    record_telemetry.assert_called_once_with("span_id", "categorical", "invalid_span")


@pytest.mark.parametrize(
    ("submitter", "exception_type", "message"),
    [
        pytest.param(None, TypeError, "dictionary containing a non-empty string id", id="not-a-dictionary"),
        pytest.param({}, TypeError, "dictionary containing a non-empty string id", id="missing-id"),
        pytest.param({"id": 123}, TypeError, "dictionary containing a non-empty string id", id="non-string-id"),
        pytest.param({"id": ""}, ValueError, "non-empty string id", id="empty-id"),
        pytest.param({"id": "user-1", "type": 123}, TypeError, r"submitter.type.*string", id="invalid-type"),
    ],
)
def test_submit_feedback_rejects_invalid_submitter(
    llmobs,
    mock_llmobs_eval_metric_writer,
    submitter,
    exception_type,
    message,
):
    with mock.patch("ddtrace.llmobs._llmobs.telemetry.record_llmobs_submit_feedback") as record_telemetry:
        with pytest.raises(exception_type, match=message):
            llmobs.submit_feedback(
                span_id="span-1",
                label="helpfulness",
                metric_type="categorical",
                value="helpful",
                submitter=submitter,
            )

    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()
    record_telemetry.assert_called_once_with("span_id", "categorical", "invalid_submitter")


@pytest.mark.parametrize(
    ("metric_type", "value"),
    [
        pytest.param("categorical", "helpful", id="categorical"),
        pytest.param("score", 1, id="integer-score"),
        pytest.param("score", 0.75, id="float-score"),
        pytest.param("boolean", True, id="boolean"),
        pytest.param("json", {"rating": 5, "reasons": ["correct"]}, id="json"),
        pytest.param("text", "The answer was clear.", id="text"),
    ],
)
def test_submit_feedback_supports_all_metric_value_pairs(
    llmobs,
    mock_llmobs_eval_metric_writer,
    metric_type,
    value,
):
    llmobs.submit_feedback(
        session_id="session-1",
        label="user_feedback",
        metric_type=metric_type,
        value=value,
        submitter={"id": "user-1"},
        ml_app="feedback-app",
    )

    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _expected_llmobs_feedback_event(
            metric_type=metric_type,
            label="user_feedback",
            value=value,
            submitter={"id": "user-1"},
            target_type="session_id",
            target_value="session-1",
            ml_app="feedback-app",
        )
    )


@pytest.mark.parametrize(
    ("metric_type", "value", "exception_type", "message", "error"),
    [
        pytest.param(
            "categorical",
            1,
            TypeError,
            "string for a categorical metric",
            "invalid_metric_value",
            id="categorical",
        ),
        pytest.param(
            "score",
            "high",
            TypeError,
            "integer or float for a score metric",
            "invalid_metric_value",
            id="score",
        ),
        pytest.param(
            "boolean",
            1,
            TypeError,
            "boolean for a boolean metric",
            "invalid_metric_value",
            id="boolean",
        ),
        pytest.param(
            "json",
            "high",
            TypeError,
            "dict for a json metric",
            "invalid_metric_value",
            id="json",
        ),
        pytest.param(
            "text",
            1,
            TypeError,
            "string for a text metric",
            "invalid_metric_value",
            id="text",
        ),
        pytest.param(
            "numerical",
            1,
            ValueError,
            "metric_type must be one of",
            "invalid_metric_type",
            id="unknown-metric-type",
        ),
    ],
)
def test_submit_feedback_rejects_invalid_metric_value_pairs(
    llmobs,
    mock_llmobs_eval_metric_writer,
    metric_type,
    value,
    exception_type,
    message,
    error,
):
    with mock.patch("ddtrace.llmobs._llmobs.telemetry.record_llmobs_submit_feedback") as record_telemetry:
        with pytest.raises(exception_type, match=message):
            llmobs.submit_feedback(
                span_id="span-1",
                label="user_feedback",
                metric_type=metric_type,
                value=value,
                submitter={"id": "user-1"},
            )

    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()
    record_telemetry.assert_called_once_with("span_id", metric_type, error)


def test_submit_evaluation_still_rejects_text_metric(llmobs, mock_llmobs_eval_metric_writer):
    with pytest.raises(
        ValueError,
        match="metric_type must be one of 'categorical', 'score', 'boolean', or 'json'.",
    ):
        llmobs.submit_evaluation(
            span={"span_id": "span-1", "trace_id": "trace-1"},
            label="comment",
            metric_type="text",
            value="This must remain unsupported.",
        )
    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()


def test_submit_feedback_optional_fields_and_agent_service_precedence(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.submit_feedback(
        feedback_join_key="order-123",
        label="helpfulness",
        metric_type="score",
        value=0.9,
        submitter={"id": "agent-1", "type": "quality-review-bot"},
        tags={"team": "support", "channel": "chat"},
        ml_app="legacy-app",
        agent_service="feedback-service",
        timestamp_ms=1756910127022,
        assessment="pass",
        reasoning="The answer solved the user's problem.",
    )

    mock_llmobs_eval_metric_writer.enqueue.assert_called_once_with(
        _expected_llmobs_feedback_event(
            metric_type="score",
            label="helpfulness",
            value=0.9,
            submitter={"id": "agent-1", "type": "quality-review-bot"},
            target_type="feedback_join_key",
            target_value="order-123",
            ml_app="feedback-service",
            timestamp_ms=1756910127022,
            tags=[
                "ddtrace.version:{}".format(ddtrace.__version__),
                "ml_app:feedback-service",
                "team:support",
                "channel:chat",
            ],
            assessment="pass",
            reasoning="The answer solved the user's problem.",
        )
    )


@pytest.mark.parametrize(
    ("optional_kwargs", "exception_type", "message", "error"),
    [
        pytest.param(
            {"timestamp_ms": -1},
            ValueError,
            "timestamp_ms must be a non-negative integer",
            "invalid_timestamp",
            id="timestamp",
        ),
        pytest.param(
            {"tags": ["invalid"]},
            Exception,
            "tags must be a dictionary",
            "invalid_tags",
            id="tags-container",
        ),
        pytest.param(
            {"tags": {1: 2}},
            Exception,
            "Tags for feedback metrics must be strings",
            "invalid_tags",
            id="tags-values",
        ),
        pytest.param(
            {"assessment": "unknown"},
            Exception,
            "assessment must be either 'pass' or 'fail'",
            "invalid_assessment",
            id="assessment",
        ),
        pytest.param(
            {"reasoning": 123},
            Exception,
            "reasoning must be a string",
            "invalid_reasoning",
            id="reasoning",
        ),
    ],
)
def test_submit_feedback_preserves_optional_field_validation(
    llmobs,
    mock_llmobs_eval_metric_writer,
    optional_kwargs,
    exception_type,
    message,
    error,
):
    with mock.patch("ddtrace.llmobs._llmobs.telemetry.record_llmobs_submit_feedback") as record_telemetry:
        with pytest.raises(exception_type, match=message):
            llmobs.submit_feedback(
                trace_id="trace-1",
                label="helpfulness",
                metric_type="categorical",
                value="helpful",
                submitter={"id": "user-1"},
                **optional_kwargs,
            )

    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()
    record_telemetry.assert_called_once_with("trace_id", "categorical", error)


def test_submit_feedback_is_noop_when_disabled(llmobs, mock_llmobs_eval_metric_writer):
    llmobs.disable()
    with mock.patch("ddtrace.llmobs._llmobs.telemetry.record_llmobs_submit_feedback") as record_telemetry:
        llmobs.submit_feedback(
            span_id="span-1",
            label="helpfulness",
            metric_type="categorical",
            value="helpful",
            submitter={"id": "user-1"},
        )

    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()
    record_telemetry.assert_not_called()


def test_submit_feedback_emits_success_telemetry(llmobs):
    with mock.patch("ddtrace.llmobs._telemetry.telemetry_writer.add_count_metric") as add_count_metric:
        llmobs.submit_feedback(
            trace_id="trace-1",
            label="comment",
            metric_type="text",
            value="The response was clear.",
            submitter={"id": "user-1"},
        )

    add_count_metric.assert_called_once_with(
        namespace=TELEMETRY_NAMESPACE.MLOBS,
        name=LLMObsTelemetryMetrics.FEEDBACK_SUBMITTED,
        value=1,
        tags=(("error", "0"), ("metric_type", "text"), ("target_type", "trace_id")),
    )


def test_submit_feedback_emits_validation_error_telemetry(llmobs, mock_llmobs_eval_metric_writer):
    with mock.patch("ddtrace.llmobs._telemetry.telemetry_writer.add_count_metric") as add_count_metric:
        with pytest.raises(TypeError, match="string for a text metric"):
            llmobs.submit_feedback(
                span_id="span-1",
                label="comment",
                metric_type="text",
                value=123,
                submitter={"id": "user-1"},
            )

    mock_llmobs_eval_metric_writer.enqueue.assert_not_called()
    add_count_metric.assert_called_once_with(
        namespace=TELEMETRY_NAMESPACE.MLOBS,
        name=LLMObsTelemetryMetrics.FEEDBACK_SUBMITTED,
        value=1,
        tags=(
            ("error", "1"),
            ("error_type", "invalid_metric_value"),
            ("metric_type", "text"),
            ("target_type", "span_id"),
        ),
    )


# ── get_spans ──────────────────────────────────────────────────────────────────


def _make_mock_response(status, body):
    """Return a mock HTTP response object compatible with Response.from_http_response."""
    mock_resp = mock.MagicMock()
    mock_resp.status = status
    mock_resp.read.return_value = json.dumps(body).encode()
    mock_resp.reason = "OK" if status == 200 else "Error"
    mock_resp.msg = None
    return mock_resp


@pytest.fixture
def mock_get_connection(llmobs):
    with mock.patch("ddtrace.llmobs._writer.HTTPConnection") as m:
        yield m


def _setup_mock_connection(mock_get_connection, pages):
    """
    pages: list of (status, body) tuples, one per paginated request.
    """
    mock_conn = mock.MagicMock()
    mock_get_connection.return_value = mock_conn
    mock_conn.getresponse.side_effect = [_make_mock_response(s, b) for s, b in pages]
    return mock_conn


def _set_get_spans_app_key(llmobs, app_key="test-app-key"):
    llmobs._app_key = app_key
    llmobs._instance._api_client._app_key = app_key


def test_get_spans_returns_span_list(mock_get_connection, llmobs):
    _set_get_spans_app_key(llmobs)
    page = {
        "data": [
            {"attributes": {"span_id": "abc", "name": "my_span", "span_kind": "llm"}},
            {"attributes": {"span_id": "def", "name": "other_span", "span_kind": "agent"}},
        ],
        "meta": {"page": {}},
    }
    _setup_mock_connection(mock_get_connection, [(200, page)])
    result = llmobs.get_spans(trace_id="trace123")
    assert len(result) == 2
    assert result[0]["span_id"] == "abc"
    assert result[1]["span_id"] == "def"


def test_get_spans_agent_service_uses_ml_app_filter(mock_get_connection, llmobs):
    _set_get_spans_app_key(llmobs)
    page = {"data": [], "meta": {"page": {}}}
    mock_conn = _setup_mock_connection(mock_get_connection, [(200, page)])
    llmobs.get_spans(ml_app="legacy_ml_app", agent_service="agent_service")

    path = mock_conn.request.call_args.args[1]
    query = urllib.parse.parse_qs(urllib.parse.urlsplit(path).query)
    assert query["filter[ml_app]"] == ["agent_service"]
    assert "filter[agent_service]" not in query


def test_get_spans_paginates(mock_get_connection, llmobs):
    _set_get_spans_app_key(llmobs)
    page1 = {
        "data": [{"attributes": {"span_id": "s1"}}],
        "meta": {"page": {"after": "cursor-xyz"}},
    }
    page2 = {
        "data": [{"attributes": {"span_id": "s2"}}],
        "meta": {"page": {}},
    }
    _setup_mock_connection(mock_get_connection, [(200, page1), (200, page2)])
    result = llmobs.get_spans(trace_id="trace123")
    assert len(result) == 2
    assert result[0]["span_id"] == "s1"
    assert result[1]["span_id"] == "s2"
    assert mock_get_connection.call_count == 2


class TestBuildSpanEventFromMetaStructE2E:
    def test_llm_span_with_messages(self, llmobs):
        with llmobs.llm(model_name="test_model", model_provider="test_provider", name="test_llm") as span:
            _annotate_llmobs_span_data(
                span,
                model_name="test_model",
                model_provider="test_provider",
                input_messages=[{"role": "user", "content": "hello"}],
                output_messages=[{"role": "assistant", "content": "hi"}],
                metadata={"temperature": 0.5},
                metrics={"input_tokens": 5, "output_tokens": 3},
            )
        assert span.error == 0
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="llm",
            model_name="test_model",
            model_provider="test_provider",
            input_messages=[{"role": "user", "content": "hello"}],
            output_messages=[{"role": "assistant", "content": "hi"}],
            metadata={"temperature": 0.5},
            metrics={"input_tokens": 5, "output_tokens": 3},
        )

    def test_task_span_with_value(self, llmobs):
        with llmobs.task(name="test_task") as span:
            _annotate_llmobs_span_data(
                span,
                input_value="some input",
                output_value="some output",
            )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="task",
            input_value="some input",
            output_value="some output",
        )

    def test_embedding_span_with_documents(self, llmobs):
        with llmobs.embedding(model_name="text-embedding-3", model_provider="openai", name="test_embedding") as span:
            _annotate_llmobs_span_data(
                span,
                model_name="text-embedding-3",
                model_provider="openai",
                input_documents=[{"text": "embed this"}],
                output_value="[0.1, 0.2]",
            )
        assert_llmobs_span_data(
            _get_llmobs_data_metastruct(span),
            span_kind="embedding",
            input_documents=[{"text": "embed this"}],
            output_value="[0.1, 0.2]",
        )

    def test_error_span(self, llmobs):
        with pytest.raises(ValueError):
            with llmobs.llm(name="test_error") as span:
                raise ValueError("something went wrong")
        assert span.error == 1
        data = _get_llmobs_data_metastruct(span)
        error = data["meta"]["error"]
        assert error["type"] == "builtins.ValueError"
        assert error["message"] == "something went wrong"


class TestExperimentScope:
    """`_dd.scope = "experiments"` must be set in meta_struct at activation time
    (not only at submit time) so downstream consumers that read meta_struct
    directly can see the scope before the span finishes.
    """

    def test_experiment_span_dd_scope_set_on_start(self, llmobs):
        with llmobs._experiment(name="root_exp", experiment_id="exp-1") as span:
            data = span._get_struct_tag(LLMOBS_STRUCT.KEY)
            assert data["_dd"]["scope"] == "experiments"

    def test_child_span_inherits_experiment_scope_on_start(self, llmobs):
        with llmobs._experiment(name="root_exp", experiment_id="exp-1"):
            with llmobs.task(name="child_task") as child:
                assert child.context.get_baggage_item(EXPERIMENT_ID_KEY) == "exp-1"
                data = child._get_struct_tag(LLMOBS_STRUCT.KEY)
                assert data["_dd"]["scope"] == "experiments"

    def test_non_experiment_span_has_no_scope_on_start(self, llmobs):
        with llmobs.task(name="standalone_task") as span:
            data = span._get_struct_tag(LLMOBS_STRUCT.KEY)
            assert "scope" not in data.get("_dd", {})


def test_annotate_messages_wrapper_with_media_on_non_llm_span(llmobs):
    """The public Messages wrapper must take the media path, not be serialized as an object repr."""
    from ddtrace.llmobs.utils import Messages

    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=Messages(
                [
                    {
                        "content": "describe",
                        "role": "user",
                        "image_parts": [{"mime_type": "image/png", "content": "QQ=="}],
                    }
                ]
            ),
        )
        llmobs._instance._prepare_llmobs_span_data(span, "agent")
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert meta["input"]["messages"] == [
        {"content": "describe", "role": "user", "image_parts": [{"mime_type": "image/png", "content": "QQ=="}]}
    ]
    assert "object at 0x" not in str(meta["input"])


def test_annotate_media_message_with_unorderable_extra_keys_does_not_raise(llmobs):
    """Unrecognized keys are logged, and a mix of key types must not make the log call raise."""
    with llmobs.agent(name="test_agent") as span:
        llmobs.annotate(
            span=span,
            input_data=[
                {
                    "content": "describe",
                    "role": "user",
                    "image_parts": [{"mime_type": "image/png", "content": "QQ=="}],
                    1: "int key",
                    "extra": "str key",
                }
            ],
        )
        llmobs._instance._prepare_llmobs_span_data(span, "agent")
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert meta["input"]["messages"][0]["image_parts"] == [{"mime_type": "image/png", "content": "QQ=="}]


@pytest.mark.parametrize("span_kind", ["agent", "workflow", "task", "tool"])
def test_annotate_malformed_media_input_falls_back_to_value(llmobs, span_kind):
    """A side that fails to parse as messages is still recorded as a value.

    Dropping it instead loses I/O that the pre-media code recorded, and under a decorator the
    annotation error is logged rather than raised, so the loss would be silent.
    """
    with getattr(llmobs, span_kind)(name="test_span") as span:
        llmobs.annotate(
            span=span,
            input_data={"image_parts": [{"url": "no mime type"}], "user_id": 5},
            output_data="the answer",
        )
        llmobs._instance._prepare_llmobs_span_data(span, span_kind)
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert "user_id" in meta["input"]["value"]
    assert "the answer" in meta["output"]["value"]


@pytest.mark.parametrize("span_kind", ["agent", "workflow", "task", "tool"])
def test_annotate_malformed_media_on_both_sides_keeps_both_as_values(llmobs, span_kind):
    """A malformed input must not suppress a malformed-but-recordable output."""
    bad = {"image_parts": [{"url": "no mime type"}], "marker": "kept"}
    with getattr(llmobs, span_kind)(name="test_span") as span:
        llmobs.annotate(span=span, input_data=bad, output_data=bad)
        llmobs._instance._prepare_llmobs_span_data(span, span_kind)
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert "kept" in meta["input"]["value"]
    assert "kept" in meta["output"]["value"]


def test_annotate_llm_span_value_does_not_clear_messages(llmobs):
    """LLM spans keep messages-wins precedence regardless of write order.

    Integrations write a value at operation end on spans a user may already have annotated with
    media, so clearing the sibling there would drop the user's payload.
    """
    from ddtrace.llmobs._utils import _annotate_llmobs_span_data

    media = [{"content": "hi", "role": "user", "image_parts": [{"mime_type": "image/png", "content": "QQ=="}]}]
    with llmobs.llm(model_name="test_model") as span:
        llmobs.annotate(span=span, input_data=media)
        _annotate_llmobs_span_data(span, input_value="integration input")
        llmobs._instance._prepare_llmobs_span_data(span, "llm")
        meta = llmobs._instance._llmobs_span_event(span)["meta"]
    assert meta["input"]["messages"] == media
