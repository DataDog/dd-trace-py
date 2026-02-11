"""Test utilities for llama_index LLMObs tests.

Provides VCR configuration for recording/replaying API responses.
"""
from pathlib import Path

import vcr


# Directory for VCR cassettes
CASSETTES_DIR = Path(__file__).parent / "cassettes"


def get_request_vcr():
    """Create VCR instance configured for llama_index API.

    First run tests with a real API key to record cassettes:
        LLAMA_INDEX_API_KEY=<real-key> pytest tests/contrib/llama_index/

    Subsequent runs will replay from cassettes.
    """
    return vcr.VCR(
        cassette_library_dir=str(CASSETTES_DIR),
        record_mode="once",
        match_on=["method", "scheme", "host", "port", "path", "query"],
        filter_headers=["authorization", "x-api-key"],
        # TODO: Add any library-specific request matchers or filters
    )
