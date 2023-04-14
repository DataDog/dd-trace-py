import os

import openai
import vcr


# VCR is used to capture and store network requests made to OpenAI.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.
# To (re)-generate the cassettes: replace this with a real key, delete the
# old cassettes and re-run the tests.
# NOTE: be sure to check the generated cassettes so they don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
openai.api_key = "<not-a-real-key>"
openai_vcr = vcr.VCR(
    cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/"),
    record_mode="once",
    match_on=["path"],
    filter_headers=["authorization", "OpenAI-Organization"],
    # Ignore requests to the agent
    ignore_localhost=True,
)
