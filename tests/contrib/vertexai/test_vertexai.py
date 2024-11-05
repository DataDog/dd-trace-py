from tests.utils import override_global_config
from vertexai.generative_models import GenerativeModel, GenerationConfig

def test_global_tags(vertexai, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    llm = GenerativeModel("gemini-1.5-flash")
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        llm.generate_content(
            "What is the argument for LeBron James being the GOAT?",
            generation_config=GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
        )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "GenerativeModel.generate_content"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("vertexai.request.model") == "gemini-1.5-flash"