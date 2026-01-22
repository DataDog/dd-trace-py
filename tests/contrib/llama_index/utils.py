"""Test utilities for llama_index LLMObs tests.

Provides VCR configuration for recording/replaying API responses.
"""

import os

import vcr


def get_request_vcr() -> vcr.VCR:
    """Create VCR instance configured for llama_index API.

    First run tests with a real API key to record cassettes:
        OPENAI_API_KEY=<real-key> pytest tests/contrib/llama_index/ -k OpenAI

    Subsequent runs will replay from cassettes.
    """
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "x-api-key", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )
