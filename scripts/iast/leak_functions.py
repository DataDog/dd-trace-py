import argparse
import asyncio
import dis
import io
import resource
import sys

import pytest

from ddtrace.appsec._iast import disable_iast_propagation
from ddtrace.appsec._iast import enable_iast_propagation
from ddtrace.appsec._iast._iast_request_context import end_iast_context
from ddtrace.appsec._iast._iast_request_context import set_iast_request_enabled
from ddtrace.appsec._iast._iast_request_context import start_iast_context
from ddtrace.appsec._iast._taint_tracking import active_map_addreses_size
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
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


def _pre_checks(module, aspect_to_check="add_aspect"):
    """Ensure the module code is replaced by IAST patching. To do that, this function inspects the bytecode"""
    dis_output = io.StringIO()
    dis.dis(module, file=dis_output)
    str_output = dis_output.getvalue()
    # Should have replaced the binary op with the aspect in add_test:
    assert f"({aspect_to_check})" in str_output


@pytest.mark.asyncio
async def iast_leaks(iterations: int, fail_percent: float, print_every: int):
    mem_reference_iterations = 50000
    if iterations < mem_reference_iterations:
        print(
            "Error: not running with %d iterations. At least 60.000 are needed to stabilize the RSS info" % iterations
        )
        sys.exit(1)

    try:
        print("Test %d iterations" % iterations)
        current_rss = 0
        half_rss = 0
        enable_iast_propagation()
        from scripts.iast.mod_leak_functions import test_doit

        # TODO(avara1986): pydantic is in the DENY_LIST, remove from it and uncomment this lines
        #  from pydantic import main
        #  _pre_checks(main, "index_aspect")

        _pre_checks(test_doit)

        for i in range(iterations):
            _start_iast_context_and_oce()
            result = await test_doit()
            assert result == "DDD_III_extend", f"result is {result}"
            assert is_pyobject_tainted(result)
            _end_iast_context_and_oce()

            if i == mem_reference_iterations:
                half_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
                print("Reference usage taken at %d iterations: %f" % (i, half_rss))

            current_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

            if i % print_every == 0:
                print(
                    f"Round {i} Max RSS: {current_rss}, Number of active maps addresses: {active_map_addreses_size()}"
                )

        final_rss = current_rss

        print(f"Round {iterations} Max RSS: {final_rss}, Number of active maps addresses: {active_map_addreses_size()}")

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
    finally:
        disable_iast_propagation()


if __name__ == "__main__":
    loop = asyncio.get_event_loop()
    args = parse_arguments()
    with override_env({"DD_IAST_ENABLED": "True"}):
        sys.exit(loop.run_until_complete(iast_leaks(args.iterations, args.fail_percent, args.print_every)))
