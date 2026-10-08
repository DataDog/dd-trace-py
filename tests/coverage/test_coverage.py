"""This file includes various tests that exercise the internal coverage collection module

Tests use the subprocess pytest mark to ensure that coverage collection happens in a clean environment.

Tests that cover import-time dependencies are meant to catch issues (important to the Intelligent Test Runner) with
lines that code technically depends on (eg: imported functions, classes, or constants), but are executed at import
time rather than at code execution time.
"""

import sys

import pytest


@pytest.mark.parametrize("mismatched_exit", [False, True])
def test_tls_fallback_skips_completed_inherited_collectors(monkeypatch, mismatched_exit):
    from contextvars import Context
    from contextvars import copy_context

    import ddtrace.internal.coverage.code as coverage_code

    monkeypatch.setattr(coverage_code, "_PY_GE_314", True)
    snapshot = Context()
    collector = object.__new__(coverage_code.ModuleCodeCollector)
    collector._coverage_enabled = False
    with coverage_code.ModuleCodeCollector.CollectInContext() as test_collector:
        with coverage_code.ModuleCodeCollector.CollectInContext() as import_collector:
            task_context = copy_context()

        nested = coverage_code.ModuleCodeCollector.CollectInContext()
        if mismatched_exit:
            child_context = task_context.copy()
            child_context.run(nested.__enter__)
            # This inherited stack does not contain the nested collector and
            # its top collector has already completed.
            task_context.run(nested.__exit__)
        else:
            task_context.run(nested.__enter__)
            task_context.run(nested.__exit__)

        # Monitoring callbacks see a snapshot without the task's ContextVars.
        snapshot.run(collector.hook_line, "/repo/active.py", 42)
        snapshot.run(collector.hook_file, "/repo/file.py")
        assert 42 in test_collector.get_covered_lines()["/repo/active.py"].to_sorted_list()
        assert "/repo/file.py" in test_collector._covered_files
        assert "/repo/active.py" not in import_collector.get_covered_lines()
        assert "/repo/active.py" not in nested.get_covered_lines()
        assert "/repo/file.py" not in import_collector.get_covered_file_paths()
        assert "/repo/file.py" not in nested.get_covered_file_paths()

    snapshot.run(collector.hook_line, "/repo/late.py", 7)
    assert "/repo/late.py" not in test_collector.get_covered_lines()


def test_coverage_stacks_are_isolated_across_copied_contexts():
    from contextvars import copy_context

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.code import ctx_collectors

    with ModuleCodeCollector.CollectInContext():
        parent_stack = ctx_collectors.get()
        parent_depth = len(parent_stack)
        child_context = copy_context()

        def collect_in_child_context():
            with ModuleCodeCollector.CollectInContext() as child:
                assert len(ctx_collectors.get()) == parent_depth + 1
                assert ctx_collectors.get()[-1] is child
                assert ctx_collectors.get()[-2] is parent_stack[-1]

        child_context.run(collect_in_child_context)
        assert ctx_collectors.get() is parent_stack
        assert len(parent_stack) == parent_depth


def test_exiting_collector_in_another_context_preserves_active_coverage():
    from contextvars import copy_context

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.code import ctx_collectors

    with ModuleCodeCollector.CollectInContext():
        parent_stack = ctx_collectors.get()
        child_context = copy_context()
        child = ModuleCodeCollector.CollectInContext()
        child_context.run(child.__enter__)

        child.__exit__()
        assert ctx_collectors.get() is parent_stack

        child_context.run(child.__exit__)
        assert ctx_collectors.get() is parent_stack


def test_completed_collector_entries_do_not_capture_inherited_context_coverage():
    """Contexts that inherit a stack still holding a completed entry must not write to it.

    A module imported inside a test may create an asyncio task before finishing its
    import collector. The task inherits the test's context, whose stack still
    references the (now completed) import entry. New coverage in the task must be
    attributed to the enclosing live collector instead of the orphaned entry.
    """
    from contextvars import copy_context

    import ddtrace.internal.coverage.code as coverage_code
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    with ModuleCodeCollector.CollectInContext() as test_collector:
        with ModuleCodeCollector.CollectInContext() as import_collector:
            task_context = copy_context()

        assert import_collector.closed

        # The task context still sees the completed import entry atop its stack.
        task_stack = task_context.run(coverage_code.ctx_collectors.get)
        assert task_stack[-1] is import_collector

        # Resolution inside the task context must skip the completed entry and
        # attribute coverage to the still-active test collector.
        assert task_context.run(coverage_code._get_ctx_covered_lines) is test_collector._covered_lines
        assert task_context.run(coverage_code._get_ctx_covered_files) is test_collector._covered_files

        # A live collector entered in the task context takes precedence even though
        # the completed import entry remains buried beneath it on the stack.
        nested = ModuleCodeCollector.CollectInContext()
        task_context.run(nested.__enter__)
        assert task_context.run(coverage_code._get_ctx_covered_lines) is nested._covered_lines
        task_context.run(nested.__exit__)
        assert task_context.run(coverage_code._get_ctx_covered_lines) is test_collector._covered_lines

    # Once every collector the task inherited has completed, new coverage lands in
    # a fresh container rather than in any of the completed entries.
    stale = task_context.run(coverage_code._get_ctx_covered_lines)
    assert stale is not import_collector._covered_lines
    assert stale is not test_collector._covered_lines


def test_mismatched_exit_resyncs_tls_fallback(monkeypatch):
    """A mismatched exit must leave snapshot callbacks recording in the active collector."""
    from contextvars import Context
    from contextvars import copy_context

    import ddtrace.internal.coverage.code as coverage_code
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    monkeypatch.setattr(coverage_code, "_PY_GE_314", True)
    snapshot = Context()
    with ModuleCodeCollector.CollectInContext() as parent:
        child_context = copy_context()
        child = ModuleCodeCollector.CollectInContext()
        child_context.run(child.__enter__)
        assert snapshot.run(coverage_code._get_ctx_covered_lines) is child._covered_lines

        child.__exit__()
        assert snapshot.run(coverage_code._get_ctx_covered_lines) is parent._covered_lines
        assert snapshot.run(coverage_code._get_ctx_covered_files) is parent._covered_files


def _armed_line_probe(path):
    """Register a private monitoring tool and return a bare collector plus its line events.

    The tool routes real sys.monitoring LINE events for a small target function into
    the collector hooks, mirroring how the instrumentation dispatches coverage events.
    Callers must invoke the returned cleanup callable when done.
    """
    import ddtrace.internal.coverage.code as coverage_code

    collector = object.__new__(coverage_code.ModuleCodeCollector)
    collector._coverage_enabled = False
    events = []

    def target():
        x = 1
        y = 2
        return x + y

    def line_callback(code_object, line_number):
        if code_object is target.__code__:
            events.append(line_number)
            collector.hook_line(path, line_number)

    # Production instrumentation prefers slot 4, so use another private slot for the probe.
    tool_id = 5
    sys.monitoring.use_tool_id(tool_id, "ddtrace-coverage-test")
    sys.monitoring.register_callback(tool_id, sys.monitoring.events.LINE, line_callback)
    sys.monitoring.set_local_events(tool_id, target.__code__, sys.monitoring.events.LINE)

    def cleanup():
        sys.monitoring.set_local_events(tool_id, target.__code__, 0)
        sys.monitoring.register_callback(tool_id, sys.monitoring.events.LINE, None)
        sys.monitoring.free_tool_id(tool_id)

    return collector, events, target, cleanup


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
def test_monitoring_callback_resolves_against_the_executing_context():
    """A real monitoring callback must record in the collector of the context running the code.

    Lines executed inside a context copied before a nested collector was entered belong to the
    copied context's own still-open collector, not to the newer collector in the entering thread.
    Resolving against the thread's latest stack instead would attribute work from one copied
    context, for example an asgiref task, to whatever scope most recently entered on that thread.
    """
    from contextvars import copy_context

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    path = "/repo/executing-context.py"
    _collector, events, target, cleanup = _armed_line_probe(path)
    try:
        with ModuleCodeCollector.CollectInContext() as outer:
            task_context = copy_context()
            with ModuleCodeCollector.CollectInContext() as nested:
                task_context.run(target)

        assert events, "the monitoring callback did not fire"
        first = target.__code__.co_firstlineno
        assert {first + 1, first + 2} <= set(outer.get_covered_lines()[path].to_sorted_list())
        assert path not in nested.get_covered_lines()
    finally:
        cleanup()


@pytest.mark.skipif(sys.version_info < (3, 14), reason="TLS fallback only applies on Python 3.14+")
def test_monitoring_callback_in_empty_context_uses_tls_fallback():
    """A monitoring callback running where the coverage ContextVars are unset must fall back to TLS.

    This complements the snapshot simulations above with the real monitoring dispatch: a fresh
    empty context cannot see any collectors, so the thread-local stack written by CollectInContext
    is the only available source for the active collector.
    """
    from contextvars import Context

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    path = "/repo/empty-context.py"
    _collector, events, target, cleanup = _armed_line_probe(path)
    try:
        with ModuleCodeCollector.CollectInContext() as test_collector:
            Context().run(target)

        assert events, "the monitoring callback did not fire"
        first = target.__code__.co_firstlineno
        assert {first + 1, first + 2} <= set(
            test_collector.get_covered_lines()[path].to_sorted_list()
        )
    finally:
        cleanup()


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess()
def test_coverage_defaults_to_file_level_when_env_unset():
    import os
    from pathlib import Path

    os.environ.pop("_DD_COVERAGE_FILE_LEVEL", None)

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path])

    from tests.coverage.included_path.lib import called_in_session

    with ModuleCodeCollector.CollectInContext() as context:
        called_in_session(1, 2)
        covered = _get_relpath_dict(cwd_path, context.get_covered_lines())

    assert covered["tests/coverage/included_path/lib.py"] == {0}


@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_coverage_import_time_lib():
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.import_time_callee import called_in_session_import_time

    ModuleCodeCollector.start_coverage()
    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    executable = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    expected_executable = {
        "tests/coverage/included_path/import_time_callee.py": {1, 2, 4, 7, 8, 10, 13, 15},
        "tests/coverage/included_path/import_time_lib.py": {1, 3, 6, 7, 8},
        "tests/coverage/included_path/nested_import_time_lib.py": {1, 4, 5, 6},
    }
    expected_covered = {
        "tests/coverage/included_path/import_time_callee.py": {2, 4},
        "tests/coverage/included_path/import_time_lib.py": {1, 3, 6, 7, 8},
        "tests/coverage/included_path/nested_import_time_lib.py": {1, 4},
    }
    expected_covered_with_imports = {
        "tests/coverage/included_path/import_time_callee.py": {1, 2, 4, 7, 13},
        "tests/coverage/included_path/import_time_lib.py": {1, 3, 6, 7, 8},
        "tests/coverage/included_path/nested_import_time_lib.py": {1, 4},
    }

    if os.getenv("_DD_COVERAGE_FILE_LEVEL") == "true":
        # In file-level mode, we only track files, not specific line numbers
        assert executable.keys() == expected_executable.keys(), (
            f"Executable files mismatch: expected={expected_executable.keys()} vs actual={executable.keys()}"
        )
        assert covered.keys() == expected_covered.keys(), (
            f"Covered files mismatch: expected={expected_covered.keys()} vs actual={covered.keys()}"
        )
        assert covered_with_imports.keys() == expected_covered_with_imports.keys(), (
            f"Covered files with imports mismatch: expected={expected_covered_with_imports.keys()}"
            f" vs actual={covered_with_imports.keys()}"
        )
    else:
        # In full coverage mode, we track exact line numbers
        assert executable == expected_executable, (
            f"Executable lines mismatch: expected={expected_executable} vs actual={executable}"
        )
        assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
        assert covered_with_imports == expected_covered_with_imports, (
            f"Covered lines with imports mismatch: expected={expected_covered_with_imports} "
            f"vs actual={covered_with_imports}"
        )


@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_coverage_import_time_function():
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # The following constant is imported, but not used, so that, by the time it is also imported in
    # calls_function_imported_in_function , it will be only be covered if the include_imported flag
    # is set to True
    from tests.coverage.included_path.imported_in_function_lib import module_level_constant  # noqa

    from tests.coverage.included_path.import_time_callee import calls_function_imported_in_function

    ModuleCodeCollector.start_coverage()
    calls_function_imported_in_function()
    ModuleCodeCollector.stop_coverage()

    lines = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance.lines)
    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = _get_relpath_dict(
        cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True)
    )

    expected_lines = {
        "tests/coverage/included_path/imported_in_function_lib.py": {1, 2, 3, 4, 7},
        "tests/coverage/included_path/import_time_callee.py": {1, 2, 4, 7, 8, 10, 13, 15},
    }
    expected_covered = {"tests/coverage/included_path/import_time_callee.py": {8, 10}}
    expected_covered_with_imports = {
        "tests/coverage/included_path/import_time_callee.py": {1, 7, 8, 10, 13},
        "tests/coverage/included_path/imported_in_function_lib.py": {1, 2, 3, 4, 7},
    }

    if os.getenv("_DD_COVERAGE_FILE_LEVEL") == "true":
        # In file-level mode, we only track files, not specific line numbers
        assert lines.keys() == expected_lines.keys(), (
            f"Executable files mismatch: expected={expected_lines.keys()} vs actual={lines.keys()}"
        )
        assert covered.keys() == expected_covered.keys(), (
            f"Covered files mismatch: expected={expected_covered.keys()} vs actual={covered.keys()}"
        )
        assert covered_with_imports.keys() == expected_covered_with_imports.keys(), (
            f"Covered files with imports mismatch: expected={expected_covered_with_imports.keys()} "
            f"vs actual={covered_with_imports.keys()}"
        )
    else:
        # In full coverage mode, we track exact line numbers
        assert lines == expected_lines, f"Executable lines mismatch: expected={expected_lines} vs actual={lines}"
        assert covered == expected_covered, f"Covered lines mismatch: expected={expected_covered} vs actual={covered}"
        assert covered_with_imports == expected_covered_with_imports, (
            f"Covered lines with imports mismatch: expected={expected_covered_with_imports} "
            f"vs actual={covered_with_imports}"
        )
