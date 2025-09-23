import types


code = """
# Gevent monkey-patching scenario that previously interacted badly with shutdown flows.
# We only import and patch, then exercise urllib3 PoolManager open/clear lifecycle.
from gevent import monkey as _monkey
_monkey.patch_all()

import urllib3


def gevent_urllib3_poolmanager() -> str:
    pm = urllib3.PoolManager(num_pools=1)
    try:
        pm.connection_from_host("localhost", port=80, scheme="http")
        ok = True
        return f"OK:{ok}"
    finally:
        pm.clear()
"""


def gevent_urllib3_poolmanager():
    """Monkey-patch gevent and use urllib3 PoolManager to simulate gevent+urllib3 usage.

    Returns an OK marker that tests can assert on, and helps ensure no tainting occurs.
    """
    module_name = "test_" + "gevent_urllib3"
    compiled_code = compile(code, "tests/appsec/integrations/packages_tests/", mode="exec")
    module_changed = types.ModuleType(module_name)
    exec(compiled_code, module_changed.__dict__)
    result = eval("gevent_urllib3_poolmanager()", module_changed.__dict__)
    return result
