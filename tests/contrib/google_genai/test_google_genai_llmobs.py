import pytest

@pytest.mark.parametrize(
    "ddtrace_global_config", [dict(_llmobs_enabled=True, _llmobs_sample_rate=1.0, _llmobs_ml_app="<ml-app-name>")]
)
class TestLLMObsGoogleGenAI:
    def test_generate_content(self, genai, mock_tracer, ):
        pass

    def test_generate_content_error():
        pass

    def test_generate_content_stream():
        pass