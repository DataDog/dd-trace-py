"""
Benchmark code provenance generation across forked child processes.

Each child process calls get_code_provenance_file() and exits.
The master process forks and waits for each child sequentially.
"""

import os
import time

import bm


class CodeProvenanceFork(bm.Scenario):
    num_children: int

    cprofile_loops: int = 0

    def _pyperf(self, loops: int) -> float:
        total = 0.0
        for _ in range(loops):
            for _ in range(self.num_children):
                t0 = time.perf_counter()
                pid = os.fork()
                if pid == 0:
                    # child
                    try:
                        from ddtrace.internal.datadog.profiling.code_provenance import get_code_provenance_file

                        get_code_provenance_file()
                    finally:
                        os._exit(0)
                else:
                    os.waitpid(pid, 0)
                    total += time.perf_counter() - t0
        return total
