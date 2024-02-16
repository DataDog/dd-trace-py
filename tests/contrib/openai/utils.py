import os

import vcr


def iswrapped(obj):
    return hasattr(obj, "__dd_wrapped__")


# VCR is used to capture and store network requests made to OpenAI.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.


# To (re)-generate the cassettes: pass a real OpenAI API key with
# OPENAI_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_openai_vcr(subdirectory_name=""):
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/%s" % subdirectory_name),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "OpenAI-Organization", "api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )
