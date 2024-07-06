import gc
import sys

from mod_leak_functions import test_doit

from ddtrace.appsec._iast._taint_tracking import create_context
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import reset_context


def test_main():
    for i in range(100):
        gc.collect()
        a = sys.gettotalrefcount()
        try:
            create_context()
            result = test_doit()  # noqa: F841
            assert is_pyobject_tainted(result)
            reset_context()
        except KeyboardInterrupt:
            print("Control-C stopped at %d rounds" % i)
            break
        gc.collect()
        print("References: %d " % (sys.gettotalrefcount() - a))


if __name__ == "__main__":
    test_main()
