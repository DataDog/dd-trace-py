"""
Integration test for multiprocessing + eval with IAST.

This test simulates the real-world scenario from dd-source that was causing
segmentation faults: a web server using multiprocessing workers with IAST enabled
performing code evaluation.
"""

from multiprocessing import Process
from multiprocessing import Queue
import os
import sys

import pytest

from ddtrace.appsec._iast._taint_tracking import OriginType
from ddtrace.appsec._iast._taint_tracking._taint_objects import taint_pyobject
from tests.appsec.iast.iast_utils import _start_iast_context_and_oce


@pytest.mark.skipif(sys.platform == "win32", reason="fork only available on Unix")
class TestMultiprocessingEvalIntegration:
    """
    Integration test combining multiprocessing and eval with IAST.

    This reproduces the dd-source test scenario that was causing segfaults.
    """

    @pytest.mark.skip(reason="multiprocessing fork doesn't work correctly in ddtrace-py 4.0")
    def test_uvicorn_style_worker_with_eval(self):
        """
        Simulate a uvicorn-style worker process that performs eval operations.

        This is the regression test for the segfault reported in dd-source
        when running with uvicorn multiprocessing workers.
        """

        def worker_process(worker_id, queue):
            """
            Simulates a web server worker that:
            1. Starts IAST context (happens on request)
            2. Performs code evaluation (like MCP handler)
            3. Should not segfault
            """
            try:
                # Initialize IAST in worker (happens automatically in real scenario)
                _start_iast_context_and_oce()

                # Simulate request handling with tainted input
                user_input = f"worker_{worker_id}_result"
                tainted_input = taint_pyobject(
                    user_input, source_name="request_param", source_value=user_input, source_origin=OriginType.PARAMETER
                )

                # Perform eval operations like MCP handler
                code = f'"{tainted_input}" + "_processed"'
                result = eval(code)

                # More complex eval scenarios
                math_expr = "2 ** 8"
                tainted_math = taint_pyobject(
                    math_expr, source_name="calculation", source_value=math_expr, source_origin=OriginType.BODY
                )
                calc_result = eval(tainted_math)

                queue.put(
                    {
                        "status": "success",
                        "worker_id": worker_id,
                        "result": result,
                        "calc_result": calc_result,
                    }
                )

            except Exception as e:
                queue.put({"status": "error", "worker_id": worker_id, "error": str(e), "type": type(e).__name__})

        # Simulate multiple workers (like uvicorn --workers 4)
        num_workers = 4
        queue = Queue()
        workers = []

        for i in range(num_workers):
            worker = Process(target=worker_process, args=(i, queue))
            workers.append(worker)
            worker.start()

        # Wait for all workers
        for worker in workers:
            worker.join(timeout=10)
            assert worker.exitcode == 0, f"Worker {worker.pid} crashed with exit code {worker.exitcode}"

        # Collect results
        results = []
        while not queue.empty():
            results.append(queue.get(timeout=1))

        # Verify all workers completed successfully
        assert len(results) == num_workers, f"Expected {num_workers} results, got {len(results)}"

        for result in results:
            assert result["status"] == "success", f"Worker {result.get('worker_id')} failed: {result}"
            assert "worker_" in result["result"]
            assert result["calc_result"] == 256

    def test_direct_fork_with_eval_no_crash(self):
        """
        Direct test using os.fork() with eval operations.

        Most direct reproduction of the segfault scenario.
        """
        _start_iast_context_and_oce()

        # Create tainted data in parent
        parent_code = "100 + 200"
        parent_tainted = taint_pyobject(
            parent_code, source_name="parent", source_value=parent_code, source_origin=OriginType.PARAMETER
        )

        # Verify parent works
        parent_result = eval(parent_tainted)
        assert parent_result == 300

        # Fork
        pid = os.fork()

        if pid == 0:
            # Child process
            try:
                # Start new IAST context in child
                _start_iast_context_and_oce()

                # Perform eval in child - this previously caused segfault
                child_code = "50 * 4"
                child_tainted = taint_pyobject(
                    child_code, source_name="child", source_value=child_code, source_origin=OriginType.BODY
                )

                child_result = eval(child_tainted)
                assert child_result == 200, f"Expected 200, got {child_result}"

                # Multiple eval calls to stress test
                for i in range(5):
                    expr = f"{i} + {i * 10}"
                    tainted_expr = taint_pyobject(
                        expr, source_name=f"test_{i}", source_value=expr, source_origin=OriginType.PARAMETER
                    )
                    _ = eval(tainted_expr)

                os._exit(0)

            except Exception as e:
                print(f"Child process error: {e}", file=sys.stderr)
                import traceback

                traceback.print_exc()
                os._exit(1)
        else:
            # Parent process
            _, status = os.waitpid(pid, 0)
            exit_code = os.WEXITSTATUS(status)
            assert exit_code == 0, f"Child process crashed with exit code {exit_code}"

            # Verify parent still works after fork
            more_parent_code = "1000 - 500"
            more_parent_tainted = taint_pyobject(
                more_parent_code, source_name="parent2", source_value=more_parent_code, source_origin=OriginType.PATH
            )
            more_parent_result = eval(more_parent_tainted)
            assert more_parent_result == 500

    @pytest.mark.skip(reason="multiprocessing fork doesn't work correctly in ddtrace-py 4.0")
    def test_sequential_workers_stress_test(self):
        """
        Stress test: Multiple workers created sequentially.

        This ensures the fork handler is stable across many fork operations.
        """

        def simple_eval_worker(queue, iteration):
            """Simple worker that just does eval."""
            try:
                _start_iast_context_and_oce()

                code = f"{iteration} * 2"
                tainted = taint_pyobject(code, "src", code, OriginType.PARAMETER)
                result = eval(tainted)

                queue.put(("success", iteration, result))
            except Exception as e:
                queue.put(("error", iteration, str(e)))

        num_iterations = 10
        queue = Queue()

        for i in range(num_iterations):
            worker = Process(target=simple_eval_worker, args=(queue, i))
            worker.start()
            worker.join(timeout=5)

            assert worker.exitcode == 0, f"Worker {i} crashed"

        # Verify all completed
        results = []
        while not queue.empty():
            results.append(queue.get(timeout=1))

        assert len(results) == num_iterations
        for status, iteration, result in results:
            assert status == "success", f"Iteration {iteration} failed"
            assert result == iteration * 2


if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s"])
