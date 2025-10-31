import types


code = """
import urllib3

def poolmanager_open_clear() -> str:
    pm = urllib3.PoolManager(num_pools=1)
    try:
        # Create a connection pool for localhost (no actual request is made)
        pm.connection_from_host("localhost", port=80, scheme="http")
        ok = True
        return f"OK:{ok}"
    finally:
        pm.clear()
"""


def urllib3_poolmanager_open_clear():
    """Exercise urllib3.PoolManager open/clear lifecycle without network IO.

    This mirrors scenarios where PoolManager is used and cleared during app shutdown.
    """
    module_name = "test_" + "urllib3"
    compiled_code = compile(code, "tests/appsec/integrations/packages_tests/", mode="exec")
    module_changed = types.ModuleType(module_name)
    exec(compiled_code, module_changed.__dict__)
    result = eval("poolmanager_open_clear()", module_changed.__dict__)
    return result
