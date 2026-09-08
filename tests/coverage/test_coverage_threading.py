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
    """Regression: ctx_covered and ctx_covered_files must be context-local, not shared defaults.

    A background thread entering its own CollectInContext must not push onto (or pop from) the
    main thread's coverage stacks. Previously ctx_covered and ctx_covered_files used default=[]
    (a single shared list object returned by ContextVar.get() in every thread), so the
    CollectInContext.__init__ ``is None`` guard never fired and every thread shared one stack.
    A thread's push/pop then corrupted the main stack and could mutate the dict that
    get_coverage_bitmaps() was iterating, raising ``RuntimeError: dictionary changed size
    during iteration``.
    """
    import os
    from pathlib import Path
    import threading

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.code import ctx_covered
    from ddtrace.internal.coverage.code import ctx_covered_files
    from ddtrace.internal.coverage.installer import install

    cwd = os.getcwd()
    install(include_paths=[Path(cwd) / "tests/coverage/included_path/"])
    ModuleCodeCollector.start_coverage()

    thread_entered = threading.Event()
    thread_can_exit = threading.Event()
    observed = {}

    with ModuleCodeCollector.CollectInContext():
        main_top_dict = ctx_covered.get()[-1]
        main_top_files = ctx_covered_files.get()[-1]

        def worker():
            # threading_coverage auto-enters a CollectInContext in _bootstrap_inner before
            # running this target, so the thread's context is active by the time we signal.
            thread_entered.set()
            from tests.coverage.included_path.callee import called_in_context_main

            called_in_context_main(1, 2)
            thread_can_exit.wait()

        t = threading.Thread(target=worker)
        t.start()
        thread_entered.wait()

        # While the thread's CollectInContext is active, the main thread must still observe its
        # OWN stacks and top entries, unaffected by the thread's push. CollectInContext.__enter__
        # appends to both ctx_covered and ctx_covered_files unconditionally, so both must remain
        # isolated. (With the buggy default=[] the thread's push lands on the shared stack and
        # the main thread sees the thread's entry on top.)
        observed["top_is_main"] = ctx_covered.get()[-1] is main_top_dict
        observed["stack_len"] = len(ctx_covered.get())
        observed["files_top_is_main"] = ctx_covered_files.get()[-1] is main_top_files
        observed["files_stack_len"] = len(ctx_covered_files.get())

        thread_can_exit.set()
        t.join()

    assert observed["top_is_main"], (
        "Main thread's ctx_covered stack was corrupted by a background thread: "
        "ctx_covered is shared across threads instead of being context-local"
    )
    assert observed["stack_len"] == 1, (
        f"Main thread's ctx_covered stack should contain only its own context, got len={observed['stack_len']}"
    )
    assert observed["files_top_is_main"], (
        "Main thread's ctx_covered_files stack was corrupted by a background thread: "
        "ctx_covered_files is shared across threads instead of being context-local"
    )
    assert observed["files_stack_len"] == 1, (
        f"Main thread's ctx_covered_files stack should contain only its own context, "
        f"got len={observed['files_stack_len']}"
    )


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
