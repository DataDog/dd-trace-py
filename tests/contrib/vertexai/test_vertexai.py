import pytest


from tests.utils import override_global_config
from tests.contrib.vertexai.utils import weather_tool

def test_global_tags(vertexai, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        llm.generate_content(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=1.0),
        )

    span = mock_tracer.pop_traces()[0][0]
    assert span.resource == "GenerativeModel.generate_content"
    assert span.service == "test-svc"
    assert span.get_tag("env") == "staging"
    assert span.get_tag("version") == "1234"
    assert span.get_tag("vertexai.request.model") == "gemini-1.5-flash"

@pytest.mark.snapshot
def test_vertexai_completion(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm.generate_content(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=0),
        )

@pytest.mark.snapshot 
def test_vertexai_completion_error(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    with pytest.raises(TypeError):
        llm.generate_content(
            "Why do bears hibernate?",
            generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=0),
            candidate_count=2, # candidate_count is not a valid keyword argument 
        )

@pytest.mark.snapshot
def test_vertexai_completion_multiple_messages(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    llm.generate_content(
        [
            {"role": "user", "parts": [{"text": "Hello World!"}]},
            {"role": "model", "parts": [{"text": "Great to meet you. What would you like to know?"}]},
            {"role": "user", "parts": [{"text": "Why do bears hibernate?"}]},
        ],
        generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=35, temperature=0),
    )

@pytest.mark.snapshot
def test_gemini_completion_system_prompt(vertexai):
    llm = vertexai.generative_models.GenerativeModel(
        "gemini-1.5-flash",
        system_instruction=[
            vertexai.generative_models.Part.from_text("You are required to insist that bears do not hibernate.")
        ],
    )
    llm.generate_content(
        "Why do bears hibernate?",
        generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=50, temperature=0),
    )

@pytest.mark.snapshot
def test_vertexai_completion_stream(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash")
    response = llm.generate_content(
        "What do you like to do for fun?",
        generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=60, temperature=0),
        stream=True,
    )
    for _ in response:
        pass

@pytest.mark.snapshot
def test_vertexai_tool_completion(vertexai):
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    llm.generate_content(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=0),
    )

@pytest.mark.snapshot
def test_vertexai_completion_tool_stream(vertexai):
    # TODO: debug output of this test; snapshot does not match local tracing results
    llm = vertexai.generative_models.GenerativeModel("gemini-1.5-flash", tools=[weather_tool])
    response = llm.generate_content(
        "What is the weather like in New York City?",
        generation_config=vertexai.generative_models.GenerationConfig(stop_sequences=["x"], max_output_tokens=30, temperature=0),
        stream=True,
    )
    for _ in response:
        pass


