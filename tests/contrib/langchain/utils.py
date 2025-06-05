import os
from uuid import UUID

from langchain_core.outputs.llm_result import LLMResult
from langchain_core.outputs.generation import Generation
from langchain_core.outputs.run_info import RunInfo

import vcr

mock_langchain_llm_generate_response = LLMResult(generations=[[Generation(text='I am a helpful assistant.', generation_info={'finish_reason': 'length', 'logprobs': None})]], llm_output={'token_usage': {'completion_tokens': 5, 'total_tokens': 10, 'prompt_tokens': 5}, 'model_name': 'gpt-3.5-turbo-instruct'}, run=[RunInfo(run_id=UUID('2f5f3cb3-e2aa-4092-b1e6-9ae1f1b2794b'))])

# VCR is used to capture and store network requests made to OpenAI and other APIs.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.


# To (re)-generate the cassettes: pass a real API key with
# {PROVIDER}_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_request_vcr():
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "OpenAI-Organization", "api-key", "x-api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )
