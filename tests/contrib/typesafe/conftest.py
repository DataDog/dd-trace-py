import os
from unittest import mock

import pytest

from ddtrace.contrib.internal.typesafe.patch import patch
from ddtrace.contrib.internal.typesafe.patch import unpatch
from ddtrace.internal.settings.standalone import standalone_config
from ddtrace.llmobs import LLMObs
from tests.contrib.typesafe.utils import MODEL
from tests.utils import override_global_config


VCR_BASE_URL = "http://127.0.0.1:9126/vcr/typesafe"


@pytest.fixture(autouse=True)
def reset_standalone_config():
    standalone_config.reset()
    yield
    standalone_config.reset()


@pytest.fixture
def typesafe_sdk():
    patch()
    import typesafe_sdk

    yield typesafe_sdk
    unpatch()


def _client_kwargs(typesafe_sdk, api_key=None):
    return {
        "api_key": api_key or os.getenv("TYPESAFE_API_KEY", "not-a-real-key"),
        "base_url": VCR_BASE_URL,
        "retry": typesafe_sdk.RetryPolicy(max_retries=0),
    }


@pytest.fixture
def typesafe_client(typesafe_sdk):
    with typesafe_sdk.TypeSafeClient(**_client_kwargs(typesafe_sdk)) as client:
        yield client


@pytest.fixture
def typesafe_client_default_model(typesafe_sdk):
    with typesafe_sdk.TypeSafeClient(model=MODEL, **_client_kwargs(typesafe_sdk)) as client:
        yield client


@pytest.fixture
def async_typesafe_client(typesafe_sdk):
    return typesafe_sdk.AsyncTypeSafeClient(**_client_kwargs(typesafe_sdk))


@pytest.fixture
def unauthenticated_typesafe_client(typesafe_sdk):
    with typesafe_sdk.TypeSafeClient(**_client_kwargs(typesafe_sdk, api_key="not-a-real-key")) as client:
        yield client


@pytest.fixture
def typesafe_llmobs():
    LLMObs.disable()
    with override_global_config({"_llmobs_ml_app": "<ml-app-name>", "_dd_api_key": "<not-a-real-key>"}):
        LLMObs.enable(integrations_enabled=False)
        LLMObs._instance._llmobs_span_writer.stop()
        LLMObs._instance._llmobs_span_writer = mock.MagicMock()
        yield LLMObs
    LLMObs.disable()
