import mock
import pytest

from ddtrace.llmobs import LLMObs
from ddtrace.llmobs._llmobs import LLMObsTraceProcessor
from tests.utils import DummyTracer
from tests.utils import override_global_config


def test_llmobs_service_enable():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>")):
        dummy_tracer = DummyTracer()
        LLMObs.enable(tracer=dummy_tracer)
        llmobs_instance = LLMObs._instance
        assert llmobs_instance is not None
        assert LLMObs.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in dummy_tracer._filters)
        LLMObs.disable()


def test_llmobs_service_disable():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>")):
        dummy_tracer = DummyTracer()
        LLMObs.enable(tracer=dummy_tracer)
        LLMObs.disable()
        assert LLMObs._instance is None
        assert LLMObs.enabled is False


def test_llmobs_service_enable_no_api_key():
    with override_global_config(dict(_dd_api_key="")):
        dummy_tracer = DummyTracer()
        with pytest.raises(ValueError):
            LLMObs.enable(tracer=dummy_tracer)
        llmobs_instance = LLMObs._instance
        assert llmobs_instance is None
        assert LLMObs.enabled is False


def test_llmobs_service_enable_already_enabled():
    with override_global_config(dict(_dd_api_key="<not-a-real-api-key>")):
        with mock.patch("ddtrace.llmobs._llmobs.log") as mock_log:
            dummy_tracer = DummyTracer()
            LLMObs.enable(tracer=dummy_tracer)
            LLMObs.enable(tracer=dummy_tracer)
        llmobs_instance = LLMObs._instance
        assert llmobs_instance is not None
        assert LLMObs.enabled
        assert llmobs_instance.tracer == dummy_tracer
        assert any(isinstance(tracer_filter, LLMObsTraceProcessor) for tracer_filter in dummy_tracer._filters)
        LLMObs.disable()
        mock_log.debug.assert_has_calls([mock.call("%s already enabled", "LLMObs")])
