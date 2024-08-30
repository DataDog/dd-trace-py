import pytest


@pytest.mark.subprocess
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


@pytest.mark.subprocess
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


@pytest.mark.subprocess
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


@pytest.mark.subprocess
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

        with concurrent.futures.ProcessPoolExecutor() as executor:
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
