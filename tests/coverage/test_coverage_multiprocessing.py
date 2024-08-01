import pytest


@pytest.mark.subprocess(parametrize={"start_method": ["fork", "forkserver", "spawn"]})
def test_coverage_multiprocessing_without_coverage():
    """Ensures that the coverage collector does not interfere with multiprocessing when it is not enabled."""
    import multiprocessing

    if __name__ == "__main__":
        import os
        from pathlib import Path

        multiprocessing.set_start_method(os.environ["start_method"], force=True)

        from ddtrace.internal.coverage.installer import install

        include_paths = [Path("/intentionally/not/valid/path")]
        install(include_paths=include_paths)

        def _sleeps():
            import time

            time.sleep(1)

        process = multiprocessing.Process(target=_sleeps())
        process.start()
        process.join()

        # This should simply not hang.


@pytest.mark.subprocess(parametrize={"start_method": ["fork", "forkserver", "spawn"]})
def test_coverage_multiprocessing_coverage_started():
    """Ensures that the coverage collector does not interfere with multiprocessing if started mid-execution"""
    import multiprocessing

    if __name__ == "__main__":
        import os
        from pathlib import Path

        multiprocessing.set_start_method(os.environ["start_method"], force=True)

        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.coverage.installer import install

        include_paths = [Path("/intentionally/not/valid/path")]
        install(include_paths=include_paths)

        def _sleeps():
            import time

            time.sleep(1)

        process = multiprocessing.Process(target=_sleeps())
        process.start()
        ModuleCodeCollector.start_coverage()
        process.join()

        # This should simply not hang.


@pytest.mark.subprocess(parametrize={"start_method": ["fork", "forkserver", "spawn"]})
def test_coverage_multiprocessing_coverage_stopped():
    """Ensures that the coverage collector does not interfere with multiprocessing if stopped mid-execution"""
    import multiprocessing

    if __name__ == "__main__":
        import os
        from pathlib import Path

        multiprocessing.set_start_method(os.environ["start_method"], force=True)

        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.coverage.installer import install

        include_paths = [Path("/intentionally/not/valid/path")]
        install(include_paths=include_paths)

        def _sleeps():
            import time

            time.sleep(1)

        process = multiprocessing.Process(target=_sleeps())
        ModuleCodeCollector.start_coverage()
        process.start()
        ModuleCodeCollector.stop_coverage()
        process.join()

        # This should simply not hang.


@pytest.mark.subprocess(parametrize={"start_method": ["fork", "forkserver", "spawn"]})
def test_coverage_multiprocessing_session():
    import multiprocessing

    if __name__ == "__main__":
        import os
        from pathlib import Path

        multiprocessing.set_start_method(os.environ["start_method"], force=True)

        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.coverage.installer import install

        cwd = os.getcwd()

        include_paths = [Path(cwd) / "tests/coverage/included_path/"]
        install(include_paths=include_paths)

        ModuleCodeCollector.start_coverage()
        from tests.coverage.included_path.callee import called_in_session_main

        process = multiprocessing.Process(target=called_in_session_main, args=(1, 2))
        process.start()
        process.join()

        ModuleCodeCollector.stop_coverage()

        covered_lines = dict(ModuleCodeCollector._instance._get_covered_lines())

        expected_lines = {
            f"{cwd}/tests/coverage/included_path/callee.py": {1, 2, 3, 5, 6, 9, 17},
            f"{cwd}/tests/coverage/included_path/lib.py": {1, 2, 5},
        }

        if expected_lines != covered_lines:
            print(f"Mismatched lines: {expected_lines} vs  {covered_lines}")
            assert False


@pytest.mark.subprocess(parametrize={"start_method": ["fork", "forkserver", "spawn"]})
def test_coverage_multiprocessing_context():
    import multiprocessing

    if __name__ == "__main__":
        import os
        from pathlib import Path

        multiprocessing.set_start_method(os.environ["start_method"], force=True)

        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.coverage.installer import install

        cwd = os.getcwd()

        include_paths = [Path(cwd) / "tests/coverage/included_path/"]
        install(include_paths=include_paths)

        from tests.coverage.included_path.callee import called_in_session_main

        called_in_session_main(1, 2)

        with ModuleCodeCollector.CollectInContext() as context_collector:
            from tests.coverage.included_path.callee import called_in_context_main

            process = multiprocessing.Process(target=called_in_context_main, args=(1, 2))
            process.start()
            process.join()

            context_covered = dict(context_collector.get_covered_lines())

        expected_lines = {
            f"{cwd}/tests/coverage/included_path/callee.py": {10, 11, 13, 14},
            f"{cwd}/tests/coverage/included_path/in_context_lib.py": {1, 2, 5},
        }

        assert expected_lines == context_covered, f"Mismatched lines: {expected_lines} vs  {context_covered}"

        session_covered = dict(ModuleCodeCollector._instance._get_covered_lines())
        assert not session_covered, f"Session recorded lines when it should not have: {session_covered}"


@pytest.mark.subprocess(parametrize={"start_method": ["fork", "forkserver", "spawn"]})
def test_coverage_concurrent_futures_processpool_session():
    import multiprocessing

    if __name__ == "__main__":
        import os

        multiprocessing.set_start_method(os.environ["start_method"], force=True)

        import concurrent.futures
        from pathlib import Path

        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.coverage.installer import install

        cwd = os.getcwd()

        include_paths = [Path(cwd) / "tests/coverage/included_path/"]
        install(include_paths=include_paths)

        ModuleCodeCollector.start_coverage()
        from tests.coverage.included_path.callee import called_in_session_main

        with concurrent.futures.ProcessPoolExecutor() as executor:
            future = executor.submit(called_in_session_main, 1, 2)
            future.result()

        ModuleCodeCollector.stop_coverage()

        covered_lines = dict(ModuleCodeCollector._instance._get_covered_lines())

        expected_lines = {
            f"{cwd}/tests/coverage/included_path/callee.py": {1, 2, 3, 5, 6, 9, 17},
            f"{cwd}/tests/coverage/included_path/lib.py": {1, 2, 5},
        }

        if expected_lines != covered_lines:
            print(f"Mismatched lines: {expected_lines} vs  {covered_lines}")
            assert False


@pytest.mark.subprocess(parametrize={"start_method": ["fork", "forkserver", "spawn"]})
def test_coverage_concurrent_futures_processpool_context():
    import multiprocessing

    if __name__ == "__main__":
        import os

        multiprocessing.set_start_method(os.environ["start_method"], force=True)

        import concurrent.futures
        from pathlib import Path

        from ddtrace.internal.coverage.code import ModuleCodeCollector
        from ddtrace.internal.coverage.installer import install

        cwd = os.getcwd()

        include_paths = [Path(cwd) / "tests/coverage/included_path/"]
        install(include_paths=include_paths)

        from tests.coverage.included_path.callee import called_in_session_main

        called_in_session_main(1, 2)

        with ModuleCodeCollector.CollectInContext() as context_collector:
            from tests.coverage.included_path.callee import called_in_context_main

            with concurrent.futures.ProcessPoolExecutor() as executor:
                future = executor.submit(called_in_context_main, 1, 2)
                future.result()

            context_covered = dict(context_collector.get_covered_lines())

        expected_lines = {
            f"{cwd}/tests/coverage/included_path/callee.py": {10, 11, 13, 14},
            f"{cwd}/tests/coverage/included_path/in_context_lib.py": {1, 2, 5},
        }

        if os.environ["start_method"] != "fork":
            # In spawn or forkserver modes, the module is reimported entirely
            expected_lines[f"{cwd}/tests/coverage/included_path/callee.py"] = {1, 9, 10, 11, 13, 14, 17}

        assert expected_lines == context_covered, f"Mismatched lines: {expected_lines} vs  {context_covered}"

        session_covered = dict(ModuleCodeCollector._instance._get_covered_lines())
        assert not session_covered, f"Session recorded lines when it should not have: {session_covered}"
