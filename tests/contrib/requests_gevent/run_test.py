"""
Patching `requests` before `gevent` monkeypatching

This is a regression test for https://github.com/DataDog/dd-trace-py/issues/506

When using `ddtrace-run` along with `requests` and `gevent` our patching causes
`requests` and `urllib3` to get loaded before `gevent` has a chance to monkey patch.

This causes `gevent` to show a warning and under certain versions cause
a maximum recursion exception to be raised.
"""
import sys

# Assert none of our modules have been imported yet
# DEV: This regression test depends on being able to control import order of these modules
# DEV: This is not entirely necessary but is a nice safe guard
assert "ddtrace" not in sys.modules
assert "gevent" not in sys.modules
assert "requests" not in sys.modules
assert "urllib3" not in sys.modules

# Import ddtrace and patch only `requests`
# DEV: We do not need to patch `gevent` for the exception to occur
from ddtrace import patch  # noqa

patch(requests=True)

# Import gevent and monkeypatch
from gevent import monkey  # noqa

monkey.patch_all()

# This is typically what will fail if `requests` (or `urllib3`)
# gets loaded before running `monkey.patch_all()`
# DEV: We are testing that no exception gets raised
import requests  # noqa

# DEV: We **MUST** use an HTTPS request, that is what causes the issue
requests.get("https://httpbin.org/get")

print("Test succeeded")
