import os

import vcr


# VCR is used to capture and store network requests.
# This is done to avoid making real calls to the API which could introduce
# flakiness and cost.


# To (re)-generate the cassettes: set environment variables for
# GOOGLE_API_KEY, GOOGLE_CLOUD_PROJECT, GOOGLE_CLOUD_LOCATION,
# and delete the old cassettes, then rerun the tests
# NOTE: be sure to check that the generated cassettes don't contain your
#       API key. Keys should be redacted by the filter_headers option below.
# NOTE: vertexai headers are not automatically filtered by vcr, so we need to
#       manually filter them.
def get_google_genai_vcr(subdirectory_name=""):
    vcr_instance = vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes/%s" % subdirectory_name),
        record_mode="once",
        match_on=["method", "scheme"],
        filter_headers=["x-goog-api-key", "authorization", "x-goog-api-client", "user-agent"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )
    return vcr_instance