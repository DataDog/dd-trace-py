import asyncio
import gc
import sys

from mod_leak_functions import test_doit

from ddtrace.appsec._iast._taint_tracking._context import create_context
from ddtrace.appsec._iast._taint_tracking._context import reset_context
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted


async def test_main():
    for i in range(1000):
        gc.collect()
        a = sys.gettotalrefcount()
        try:
            create_context()
            result = await test_doit()  # noqa: F841
            assert is_pyobject_tainted(result)
            reset_context()
        except KeyboardInterrupt:
            print("Control-C stopped at %d rounds" % i)
            break
        gc.collect()
        print("References: %d " % (sys.gettotalrefcount() - a))


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    sys.exit(loop.run_until_complete(test_main()))
