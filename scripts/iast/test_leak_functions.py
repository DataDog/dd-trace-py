import argparse
import resource
import sys

from ddtrace.appsec._iast._iast_request_context import end_iast_context
from ddtrace.appsec._iast._iast_request_context import set_iast_request_enabled
from ddtrace.appsec._iast._iast_request_context import start_iast_context
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from tests.appsec.iast.aspects.conftest import _iast_patched_module
from tests.utils import override_env


def _start_iast_context_and_oce():
    start_iast_context()
    set_iast_request_enabled(True)


def _end_iast_context_and_oce():
    end_iast_context()


def parse_arguments():
    parser = argparse.ArgumentParser(description="Memory leak test script.")
    parser.add_argument("--iterations", type=int, default=100000, help="Number of iterations.")
    parser.add_argument(
        "--fail_percent", type=float, default=2.0, help="Failure threshold for memory increase percentage."
    )
    parser.add_argument("--print_every", type=int, default=250, help="Print status every N iterations.")
    return parser.parse_args()


def test_iast_leaks(iterations: int, fail_percent: float, print_every: int):
    if iterations < 60000:
        print(
            "Error: not running with %d iterations. At least 60.000 are needed to stabilize the RSS info" % iterations
        )
        sys.exit(1)

    try:
        mem_reference_iterations = 50000
        print("Test %d iterations" % iterations)
        current_rss = 0
        half_rss = 0

        mod = _iast_patched_module("scripts.iast.mod_leak_functions")
        test_doit = mod.test_doit

        for i in range(iterations):
            _start_iast_context_and_oce()
            result = test_doit()  # noqa: F841
            assert result == "DDD_III_extend", f"result is {result}"  # noqa: F841
            assert is_pyobject_tainted(result)
            _end_iast_context_and_oce()

            if i == mem_reference_iterations:
                half_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                print("Reference usage taken at %d iterations: %f" % (i, half_rss))

            current_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

            if i % print_every == 0:
                print(f"Round {i} Max RSS: {current_rss}")

        final_rss = current_rss

        print(f"Round {iterations} Max RSS: {final_rss}")

        percent_increase = ((final_rss - half_rss) / half_rss) * 100
        if percent_increase > fail_percent:
            print(
                f"Failed: memory increase from reference-point ({mem_reference_iterations} iterations) is "
                f"{percent_increase:.2f}% which is greater than {fail_percent}%"
            )
            return 1

        print(
            f"Success: memory increase is {percent_increase:.2f}% from reference-point ({mem_reference_iterations} "
            f"iterations) which is less than {fail_percent}%"
        )
        return 0

    except KeyboardInterrupt:
        print("Test interrupted.")


if __name__ == "__main__":
    args = parse_arguments()
    with override_env({"DD_IAST_ENABLED": "True"}):
        sys.exit(test_iast_leaks(args.iterations, args.fail_percent, args.print_every))
