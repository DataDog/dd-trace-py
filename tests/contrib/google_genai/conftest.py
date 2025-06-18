import os

import mock
import pytest

from ddtrace.contrib.internal.google_genai.patch import patch
from ddtrace.contrib.internal.google_genai.patch import unpatch
from ddtrace.trace import Pin
from tests.utils import DummyTracer
from tests.utils import DummyWriter


@pytest.fixture
def mock_tracer(genai):
    try:
        pin = Pin.get_from(genai)
        mock_tracer = DummyTracer(writer=DummyWriter(trace_flush_enabled=False))
        pin._override(genai, tracer=mock_tracer)
        yield mock_tracer
    except Exception:
        yield


@pytest.fixture
def mock_google_auth():
    with mock.patch("google.auth.default") as mock_auth_default:
        mock_credentials = mock.Mock()
        mock_credentials.expired = False
        mock_credentials.valid = True
        mock_credentials.token = "mock-access-token-for-vcr"
        mock_credentials.refresh = mock.Mock()
        mock_credentials.quota_project_id = None
        mock_credentials.service_account_email = None
        mock_credentials.project_id = None
        mock_credentials.signer = None
        mock_auth_default.return_value = (mock_credentials, os.environ.get("GOOGLE_CLOUD_PROJECT", "test-project"))
        yield mock_auth_default


@pytest.fixture
def genai():
    patch()
    from google import genai

    # When testing locally to generate new cassette files,
    # comment the lines below to use the real Google API key and project/location
    os.environ["GOOGLE_API_KEY"] = "<not-a-real-key>"
    os.environ["GOOGLE_CLOUD_PROJECT"] = "<not-a-real-project>"
    os.environ["GOOGLE_CLOUD_LOCATION"] = "<not-a-real-location>"

    yield genai
    unpatch()
