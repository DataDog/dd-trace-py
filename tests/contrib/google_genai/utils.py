import os

import vcr


# VCR is used to capture and store network requests.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.


# To (re)-generate the cassettes: pass a real API key with
# GOOGLE_API_KEY, delete the old cassettes and re-run the tests.
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: that different cassettes have to be used between sync and async
#       due to this issue: https://github.com/kevin1024/vcrpy/issues/463
#       between cassettes generated for requests and aiohttp.
def get_google_genai_vcr(subdirectory_name=""):
    vcr_instance = vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/%s" % subdirectory_name),
        record_mode="once",
        # these match options
        match_on=["method", "scheme", "port"],
        filter_headers=["x-goog-api-key", "authorization", "x-goog-api-client", "user-agent"],
        # Ignore requests to the agent
        ignore_localhost=True,
        ignore_hosts=["oauth2.googleapis.com"],
    )
    vcr_instance.register_matcher("custom_path_matcher", custom_path_matcher)
    vcr_instance.match_on = ["custom_path_matcher"]
    return vcr_instance


def custom_path_matcher(r1, r2):
    assert r1.method == r2.method
    assert r1.scheme == r2.scheme
    if "not-a-real-location" in r1.path or "not-a-real-location" in r2.path:
        return
    assert r1.path == r2.path
