import os

import vcr


logs_vcr = vcr.VCR(
    cassette_library_dir=os.path.join(os.path.dirname(__file__), "llmobs_cassettes/"),
    record_mode="once",
    match_on=["path"],
    filter_headers=[("DD-API-KEY", "XXXXXX")],
    # Ignore requests to the agent
    ignore_localhost=True,
)
