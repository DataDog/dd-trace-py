import os

import vcr


long_input_text = """
I have convinced myself that there is absolutely nothing in the world, no sky, no earth, no minds, no
bodies. Does it now follow that I too do not exist? No: if I convinced myself of something then I certainly
existed. But there is a deceiver of supreme power and cunning who is deliberately and constantly deceiving
me. In that case I too undoubtedly exist, if he is deceiving me; and let him deceive me as much as he can,
he will never bring it about that I am nothing so long as I think that I am something. So after considering
everything very thoroughly, I must finally conclude that this proposition, I am, I exist, is necessarily
true whenever it is put forward by me or conceived in my mind.
"""


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
def get_request_vcr(subdirectory_name=""):
    return vcr.VCR(
        cassette_library_dir=os.path.join(os.path.dirname(__file__), "cassettes"),
        record_mode="once",
        match_on=["path"],
        filter_headers=["authorization", "OpenAI-Organization", "api-key", "x-api-key"],
        # Ignore requests to the agent
        ignore_localhost=True,
    )
