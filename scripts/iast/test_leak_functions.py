import ddtrace.auto  # noqa: F401  # isort: skip
import resource
import sys

from mod_leak_functions import test_doit
from ddtrace.appsec._iast._taint_tracking import create_context, is_pyobject_tainted, reset_context
import argparse


def parse_arguments():
    parser = argparse.ArgumentParser(description="Memory leak test script.")
    parser.add_argument("--mode", choices=["ci", "console"], default="console", help="Mode of operation.")
    parser.add_argument("--iterations", type=int, default=100000, help="Number of iterations.")
    parser.add_argument(
        "--failPercent", type=float, default=2.0, help="Failure threshold for memory increase percentage."
    )
    parser.add_argument("--printEvery", type=int, help="Print status every N iterations.")
    parser.add_argument(
        "--graph", type=lambda x: (str(x).lower() == "true"), default=True, help="Enable ASCII graph output."
    )

    args = parser.parse_args()

    # Set default for printEvery if not provided
    if args.printEvery is None:
        args.printEvery = 1000 if args.mode == "ci" else 250

    return args


def test_iast_leaks():
    args = parse_arguments()

    try:
        rss_list = []
        half_iterations = args.iterations // 2
        print("Test %d iterations" % args.iterations)
        current_rss = 0
        half_rss = 0

        for i in range(args.iterations):
            create_context()
            result = test_doit()  # noqa: F841
            assert is_pyobject_tainted(result)
            reset_context()

            if i == half_iterations:
                half_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024

            current_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
            if args.graph:
                rss_list.append(int(current_rss))

            if i % args.printEvery == 0:
                print(f"Round {i} Max RSS: {current_rss}")

        final_rss = current_rss

        print(f"Round {args.iterations} Max RSS: {final_rss}")

        if args.graph:
            # TODO: write an ascii graph
            pass

        percent_increase = ((final_rss - half_rss) / half_rss) * 100
        if percent_increase > args.failPercent:
            print(
                f"Failed: memory increase from half-point ({half_iterations} iterations) is {percent_increase:.2f}% which is greater than {args.failPercent}%"
            )
            return 1
        else:
            print(
                f"Success: memory increase is {percent_increase:.2f}% from half-point ({half_iterations} iterations) which is less than {args.failPercent}%"
            )
            return 0

    except KeyboardInterrupt:
        print("Test interrupted.")


if __name__ == "__main__":
    sys.exit(test_iast_leaks())
