import ddtrace.auto  # noqa: F401  # isort: skip
import resource
import sys

from mod_leak_functions import test_doit

from ddtrace.appsec._iast._taint_tracking import create_context
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.appsec._iast._taint_tracking import reset_context


def test_main():
    try:
        rounds = int(sys.argv[1])
    except ValueError:
        rounds = 1
    print("Test %d rounds" % rounds)
    for i in range(rounds):
        try:
            create_context()
            result = test_doit()  # noqa: F841
            assert is_pyobject_tainted(result)
            reset_context()
        except KeyboardInterrupt:
            print("Control-C stopped at %d rounds" % i)
            break
        if i % 250 == 0:
            print("Round %d Max RSS: " % i, end="")
            print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    print("Round %d Max RSS: " % rounds, end="")
    print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


if __name__ == "__main__":
    test_main()
