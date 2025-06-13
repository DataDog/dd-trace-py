import pytest

from google.genai import types

from tests.utils import override_global_config
from tests.contrib.google_genai.utils import get_google_genai_vcr


@pytest.fixture(scope="session")
def google_genai_vcr():
    yield get_google_genai_vcr(subdirectory_name="v1")


def test_global_tags(google_genai_vcr, genai, mock_tracer):
    """
    When the global config UST tags are set
        The service name should be used for all data
        The env should be used for all data
        The version should be used for all data
    """
    with override_global_config(dict(service="test-svc", env="staging", version="1234")):
        with google_genai_vcr.use_cassette("generate_content.yaml"):
            client = genai.Client()
            response = client.models.generate_content(
                model='gemini-2.0-flash-001', 
                contents='Why is the sky blue? Explain in 2-3 sentences.',
                config=types.GenerateContentConfig(
                    temperature=0,
                    top_p=0.95,
                    top_k=20,
                    candidate_count=1,
                    seed=5,
                    max_output_tokens=100,
                    stop_sequences=['STOP!'],
                    presence_penalty=0.0,
                    frequency_penalty=0.0,
                ),
            )

        span = mock_tracer.pop_traces()[0][0]
        assert span.resource == "Models.generate_content"
        assert span.service == "test-svc"
        assert span.get_tag("env") == "staging"
        assert span.get_tag("version") == "1234"
        assert span.get_tag("google_genai.request.model") == "gemini-2.0-flash-001"
        assert span.get_tag("google_genai.request.provider") == "google"


@pytest.mark.snapshot(token="tests.contrib.google_genai.test_google_genai.test_google_genai_completion")
def test_google_genai_completion(google_genai_vcr, genai):
    with google_genai_vcr.use_cassette("generate_content.yaml"):
        client = genai.Client()
        response = client.models.generate_content(
            model='gemini-2.0-flash-001', 
            contents='Why is the sky blue? Explain in 2-3 sentences.',
            config=types.GenerateContentConfig(
                temperature=0,
                top_p=0.95,
                top_k=20,
                candidate_count=1,
                seed=5,
                max_output_tokens=100,
                stop_sequences=['STOP!'],
                presence_penalty=0.0,
                frequency_penalty=0.0,
            ),
        )


@pytest.mark.snapshot(token="tests.contrib.google_genai.test_google_genai.test_google_genai_completion_error")
def test_google_genai_completion_error(google_genai_vcr, genai):
    with pytest.raises(TypeError):
        client = genai.Client()
        response = client.models.generate_content(
            model='gemini-2.0-flash-001', 
            contents='Why is the sky blue? Explain in 2-3 sentences.',
            not_an_argument='why am i here?', #invalid argument
        )
