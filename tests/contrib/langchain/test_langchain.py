import os

import pytest
import vcr

from ddtrace.contrib.langchain.patch import patch
from ddtrace.contrib.langchain.patch import unpatch


@pytest.fixture(autouse=True)
def patch_langchain():
    patch()
    yield
    unpatch()


def get_openai_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "OpenAI-Organization"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )


@pytest.fixture(scope="session")
def openai_vcr():
    yield get_openai_vcr()


# @pytest.mark.snapshot()
# def test_langchain_openai(openai_vcr):
#     from langchain.llms import OpenAI
#     # LangChain allows a common and simple interface to interact with different LLM providers
#     llm = OpenAI()
#     with openai_vcr.use_cassette("openai_completion.yaml"):
#         llm("Can you explain what Descartes meant by 'I think, therefore I am'?")
