import pytest


@pytest.mark.subprocess(env={"_DD_COVERAGE_FILE_LEVEL": "false"})
def test_coverage_threading_session():
    import os
    from pathlib import Path
    import threading

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd = os.getcwd()

    include_paths = [Path(cwd) / "tests/coverage/included_path/"]
    install(include_paths=include_paths)

    ModuleCodeCollector.start_coverage()
    from tests.coverage.included_path.callee import called_in_session_main

    thread = threading.Thread(target=called_in_session_main, args=(1, 2))
    thread.start()
    thread.join()

    ModuleCodeCollector.stop_coverage()

    covered_lines = _get_relpath_dict(cwd, ModuleCodeCollector._instance._get_covered_lines())

    expected_lines = {
        "tests/coverage/included_path/callee.py": {1, 2, 3, 5, 6, 9, 17},
        "tests/coverage/included_path/lib.py": {1, 2, 5},
    }

    if expected_lines != covered_lines:
        print(f"Mismatched lines: {expected_lines} vs  {covered_lines}")
        assert False


@pytest.mark.subprocess(env={"_DD_COVERAGE_FILE_LEVEL": "false"})
def test_coverage_threading_context():
    import os
    from pathlib import Path
    import threading

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd = os.getcwd()

    include_paths = [Path(cwd) / "tests/coverage/included_path/"]
    install(include_paths=include_paths)

    from tests.coverage.included_path.callee import called_in_session_main

    called_in_session_main(1, 2)

    with ModuleCodeCollector.CollectInContext() as context_collector:
        from tests.coverage.included_path.callee import called_in_context_main

        thread = threading.Thread(target=called_in_context_main, args=(1, 2))
        thread.start()
        thread.join()

        context_covered = _get_relpath_dict(cwd, context_collector.get_covered_lines())

    expected_lines = {
        "tests/coverage/included_path/callee.py": {10, 11, 13, 14},
        "tests/coverage/included_path/in_context_lib.py": {1, 2, 5},
    }

    assert expected_lines == context_covered, f"Mismatched lines: {expected_lines} vs  {context_covered}"

    session_covered = dict(ModuleCodeCollector._instance._get_covered_lines())
    assert not session_covered, f"Session recorded lines when it should not have: {session_covered}"


@pytest.mark.subprocess(env={"_DD_COVERAGE_FILE_LEVEL": "false"})
def test_coverage_concurrent_futures_threadpool_session():
    import concurrent.futures
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd = os.getcwd()

    include_paths = [Path(cwd) / "tests/coverage/included_path/"]
    install(include_paths=include_paths)

    ModuleCodeCollector.start_coverage()
    from tests.coverage.included_path.callee import called_in_session_main

    with concurrent.futures.ThreadPoolExecutor() as executor:
        future = executor.submit(called_in_session_main, 1, 2)
        future.result()

    ModuleCodeCollector.stop_coverage()

    covered_lines = _get_relpath_dict(cwd, ModuleCodeCollector._instance._get_covered_lines())

    expected_lines = {
        "tests/coverage/included_path/callee.py": {1, 2, 3, 5, 6, 9, 17},
        "tests/coverage/included_path/lib.py": {1, 2, 5},
    }

    if expected_lines != covered_lines:
        print(f"Mismatched lines: {expected_lines} vs  {covered_lines}")
        assert False


@pytest.mark.subprocess(env={"_DD_COVERAGE_FILE_LEVEL": "false"})
def test_coverage_context_isolated_across_threads():
    """A worker collecting coverage must not share the parent's context stacks."""
    import os
    from pathlib import Path
    import threading

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.code import ctx_covered
    from ddtrace.internal.coverage.code import ctx_covered_files
    from ddtrace.internal.coverage.installer import install

    cwd = os.getcwd()
    install(include_paths=[Path(cwd) / "tests/coverage/included_path/"])
    thread_entered = threading.Event()
    thread_can_exit = threading.Event()

    with ModuleCodeCollector.CollectInContext():
        main_lines_stack = ctx_covered.get()
        main_files_stack = ctx_covered_files.get()
        main_lines = main_lines_stack[-1]
        main_files = main_files_stack[-1]

        def worker():
            # The patched _bootstrap_inner enters a coverage context before calling the target.
            from tests.coverage.included_path.callee import called_in_context_main

            called_in_context_main(1, 2)
            thread_entered.set()
            thread_can_exit.wait(timeout=10)

        thread = threading.Thread(target=worker)
        thread.start()
        try:
            assert thread_entered.wait(timeout=5), "Worker did not enter its coverage context"
            # On Python 3.14 the target runs in a snapshot context, so inspect the
            # parent's stacks while the patched bootstrap's context is still active.
            assert hasattr(thread, "_coverage_context")
            assert ctx_covered.get() is main_lines_stack
            assert ctx_covered_files.get() is main_files_stack
            assert len(main_lines_stack) == len(main_files_stack) == 1
            assert main_lines_stack[-1] is main_lines
            assert main_files_stack[-1] is main_files
        finally:
            thread_can_exit.set()
            thread.join(timeout=5)

        assert not thread.is_alive(), "Worker did not exit its coverage context"
        assert main_lines_stack[-1] is main_lines
        assert main_files_stack[-1] is main_files


@pytest.mark.subprocess(env={"_DD_COVERAGE_FILE_LEVEL": "false"})
def test_coverage_concurrent_futures_threadpool_context():
    import concurrent.futures
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd = os.getcwd()

    include_paths = [Path(cwd) / "tests/coverage/included_path/"]
    install(include_paths=include_paths)

    from tests.coverage.included_path.callee import called_in_session_main

    called_in_session_main(1, 2)

    with ModuleCodeCollector.CollectInContext() as context_collector:
        from tests.coverage.included_path.callee import called_in_context_main

        with concurrent.futures.ThreadPoolExecutor() as executor:
            future = executor.submit(called_in_context_main, 1, 2)
            future.result()

        context_covered = _get_relpath_dict(cwd, context_collector.get_covered_lines())

    expected_lines = {
        "tests/coverage/included_path/callee.py": {10, 11, 13, 14},
        "tests/coverage/included_path/in_context_lib.py": {1, 2, 5},
    }

    assert expected_lines == context_covered, f"Mismatched lines: {expected_lines} vs  {context_covered}"

    session_covered = dict(ModuleCodeCollector._instance._get_covered_lines())
    assert not session_covered, f"Session recorded lines when it should not have: {session_covered}"
