import os

import pytest
import vcr

from ddtrace.contrib.internal.pydantic_ai.patch import patch
from ddtrace.contrib.internal.pydantic_ai.patch import unpatch


@pytest.fixture(autouse=True)
def pydantic_ai(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "<not-a-real-key>")
    patch()
    import pydantic_ai

    yield pydantic_ai
    unpatch()


@pytest.fixture
def request_vcr(ignore_localhost=True):
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        ignore_localhost=ignore_localhost,
    )
