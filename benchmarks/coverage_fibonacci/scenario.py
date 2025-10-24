"""
Benchmark for coverage collection on recursive code.

This benchmark ensures that the sys.monitoring.DISABLE optimization
doesn't regress. The DISABLE return value prevents the handler from being
called repeatedly for the same line in recursive functions and loops.

Without DISABLE: Handler called on every line execution
With DISABLE: Handler called once per unique line
"""

from typing import Callable
from typing import Generator

import bm


class CoverageFibonacci(bm.Scenario):
    """
    Benchmark coverage collection performance on recursive and iterative code.

    Tests the DISABLE optimization: returning sys.monitoring.DISABLE prevents
    the handler from being called repeatedly for the same line.
    """

    fib_n_recursive: int

    def run(self) -> Generator[Callable[[int], None], None, None]:
        import os
        from pathlib import Path

        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.coverage.installer import install

        # Install coverage
        install(include_paths=[Path(os.getcwd())])

        # Import after installation
        from utils import fibonacci_recursive

        def _(loops: int) -> None:
            for _ in range(loops):
                # Use coverage context to simulate real pytest per-test coverage
                with ModuleCodeCollector.CollectInContext():
                    # Recursive: Many function calls, same lines executed repeatedly
                    result = fibonacci_recursive(self.fib_n_recursive)

                    # Verify correctness (don't optimize away)
                    assert result > 0

        yield _
