import os

import openai
import pytest
import vcr

from ddtrace.contrib.openai.patch import patch
from ddtrace.contrib.openai.patch import unpatch


# To (re)-generate the cassettes: replace this with a real key, delete the
# old cassettes and re-run the tests.
# NOTE: be sure to check the generated cassettes so they don't contain your
#       API key.
openai.api_key = "<not-a-real-key>"


@pytest.fixture(autouse=True)
def patch_openai():
    patch()
    yield
    unpatch()


openai_vcr = vcr.VCR(
    cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/"),
    record_mode="once",
    match_on=["path"],
    # Ignore requests to the agent
    ignore_localhost=True,
)


@pytest.mark.snapshot
def test_completion():
    with openai_vcr.use_cassette("hello_world.yaml"):
        openai.Completion.create(model="ada", prompt="Hello world")
