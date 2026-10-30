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
    import ddtrace.internal.coverage.context_state as coverage_context_state

    monkeypatch.setattr(coverage_code, "_PY_GE_314", True)
    monkeypatch.setattr(coverage_context_state, "_PY_GE_314", True)
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


def test_expired_copied_context_does_not_borrow_the_thread_collector(monkeypatch):
    from contextvars import Context
    from contextvars import copy_context

    import ddtrace.internal.coverage.code as coverage_code
    import ddtrace.internal.coverage.context_state as coverage_context_state

    monkeypatch.setattr(coverage_code, "_PY_GE_314", True)
    monkeypatch.setattr(coverage_context_state, "_PY_GE_314", True)
    monkeypatch.setattr(coverage_context_state._tls_coverage, "stack", (), raising=False)
    collector = object.__new__(coverage_code.ModuleCodeCollector)
    collector._coverage_enabled = False

    def _run():
        with coverage_code.ModuleCodeCollector.CollectInContext() as first:
            # This copy is made while the first collector is active, so it keeps the
            # coverage ContextVars even after that collector completes.
            expired = copy_context()

        with coverage_code.ModuleCodeCollector.CollectInContext() as second:
            # Late work in the expired copy must not be attributed to the thread's
            # new collector, so it is dropped like on Python versions before 3.14.
            expired.run(collector.hook_line, "/repo/late.py", 1)
            expired.run(collector.hook_file, "/repo/late_file.py")
            assert "/repo/late.py" not in second.get_covered_lines()
            assert "/repo/late_file.py" not in second.get_covered_file_paths()
            assert "/repo/late.py" not in first.get_covered_lines()
            assert "/repo/late_file.py" not in first.get_covered_file_paths()

            # A context without any coverage state still falls back to the thread's
            # active collector.
            Context().run(collector.hook_line, "/repo/fresh.py", 2)
            assert 2 in second.get_covered_lines()["/repo/fresh.py"].to_sorted_list()

            stale_lines = expired.run(coverage_code._get_ctx_covered_lines)
            stale_files = expired.run(coverage_code._get_ctx_covered_files)
            assert not stale_lines
            assert not stale_files

    # Run in a context without coverage state, so a collector that is already active around the
    # test (for example the ddtrace pytest plugin's per-test coverage) does not keep the copied
    # context alive.
    Context().run(_run)


# TODO: the TLS fallback is a best-effort mirror of the stack of whichever context last entered or exited a
# collector, and Context.run does not restore thread-local state. A fix needs to resynchronize the mirror when
# execution returns from a copied context without restoring stale stacks for inherited collectors; saving and
# restoring the stack on enter and exit was tried and broke that inherited-collector case.
@pytest.mark.xfail(strict=True, reason="Known limitation: the TLS fallback is not resynchronized after Context.run")
def test_returning_from_a_copied_context_restores_the_tls_fallback(monkeypatch):
    from contextvars import Context
    from contextvars import copy_context

    import ddtrace.internal.coverage.code as coverage_code
    import ddtrace.internal.coverage.context_state as coverage_context_state

    monkeypatch.setattr(coverage_code, "_PY_GE_314", True)
    monkeypatch.setattr(coverage_context_state, "_PY_GE_314", True)
    collector = object.__new__(coverage_code.ModuleCodeCollector)
    collector._coverage_enabled = False
    with coverage_code.ModuleCodeCollector.CollectInContext() as outer:
        # The copy is made while only the outer collector is active.
        task_context = copy_context()
        with coverage_code.ModuleCodeCollector.CollectInContext() as nested:
            # This scope is entered and exited entirely inside the copied context
            # while the thread has its own nested collector active.
            inner = coverage_code.ModuleCodeCollector.CollectInContext()
            task_context.run(inner.__enter__)
            task_context.run(inner.__exit__)

            # Returning from the copied context should leave the TLS fallback aligned
            # with the thread's flow, so a fresh context still resolves to the
            # nested collector instead of the copied context's remaining stack.
            Context().run(collector.hook_line, "/repo/fresh.py", 1)
            assert 1 in nested.get_covered_lines()["/repo/fresh.py"].to_sorted_list()
            assert "/repo/fresh.py" not in outer.get_covered_lines()
            assert "/repo/fresh.py" not in inner.get_covered_lines()


# TODO: the unittest integration enters one shared CollectInContext for every test
# (ddtrace/contrib/internal/unittest/patch.py), and __enter__ resets the closed flag, so a context copied
# during one test becomes active again when the next test starts and its late work lands in that test's
# coverage. A fix could give each test a fresh collector, or tag stack entries with a per-enter generation
# so that reopening a collector does not reactivate stacks from its previous scope.
@pytest.mark.xfail(strict=True, reason="Known limitation: re-entering a collector reactivates contexts copied earlier")
def test_reused_collector_does_not_reactivate_contexts_from_its_previous_scope():
    from contextvars import copy_context

    import ddtrace.internal.coverage.code as coverage_code

    collector = object.__new__(coverage_code.ModuleCodeCollector)
    collector._coverage_enabled = False
    shared = coverage_code.ModuleCodeCollector.CollectInContext()
    with shared:
        # A context copied during the first test, for example by a task that outlives it.
        retained = copy_context()

    with shared:
        # Late work from the first test's context should not count as coverage of the second test.
        retained.run(collector.hook_line, "/repo/late.py", 1)
        assert "/repo/late.py" not in shared.get_covered_lines()


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
    import ddtrace.internal.coverage.context_state as coverage_context_state

    monkeypatch.setattr(coverage_context_state, "_PY_GE_314", True)
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

    # Production instrumentation tries slots 4, 3, and 1, so prefer slots it never claims and
    # fall back to any remaining free slot the way register_coverage does.
    tool_id = None
    for slot in (5, 2, 0):
        try:
            sys.monitoring.use_tool_id(slot, "ddtrace-coverage-test")
        except ValueError:
            continue
        tool_id = slot
        break
    if tool_id is None:
        raise RuntimeError("no sys.monitoring tool slot available for the test probe")

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
        assert {first + 1, first + 2} <= set(test_collector.get_covered_lines()[path].to_sorted_list())
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


@pytest.mark.subprocess()
def test_import_time_covered_survives_symlinked_co_filename():
    """Import-time coverage must be recorded even when co_filename differs from realpath.

    _enter_module_context keys the import context by co_filename (what the hooks record
    under), while _finish_module_context looks up module.__file__ and realpath(). A
    symlinked workspace (or Python < 3.10 where __file__ is not resolved) must not
    silently lose the module's own lines.
    """
    import os
    from pathlib import Path
    import sys
    import tempfile

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as tmpdir:
        real_dir = Path(tmpdir) / "real"
        real_dir.mkdir()
        (real_dir / "symmod.py").write_text("value = 1\n")
        link_dir = Path(tmpdir) / "link"
        os.symlink(real_dir, link_dir)
        sys.path.insert(0, str(link_dir))
        try:
            install(include_paths=[Path(tmpdir)], collect_import_time_coverage=True)
            import symmod  # noqa: F401

            collector = ModuleCodeCollector._instance
            canonical = os.path.realpath(str(real_dir / "symmod.py"))
            assert canonical in collector._import_time_covered, (
                "import-time coverage lost: %s not in _import_time_covered" % canonical
            )
        finally:
            sys.path.remove(str(link_dir))


@pytest.mark.subprocess()
def test_obfuscated_code_logs_warning():
    """Obfuscated code objects (e.g. PyArmor) must produce a warning, not silence.

    The pre-refactor instrument_code logged a warning when it encountered an
    obfuscated module; the combined condition in instrument_module must preserve it.
    Verifies the obfuscation check is a separate branch (not combined with the
    dedup check) and that the warning is emitted.
    """
    import inspect

    from ddtrace.internal.coverage.instrumented_mode import InstrumentedCoverage

    source = inspect.getsource(InstrumentedCoverage.instrument_module)
    assert "is_obfuscated_code(code)" in source, "obfuscation check must be present"
    assert "log.warning" in source, "obfuscation check must log a warning"
    for line in source.split("\n"):
        if "is_obfuscated_code" in line:
            assert "or" not in line, "obfuscated code must not be combined with the dedup check: %r" % line


@pytest.mark.subprocess()
def test_nested_imports_through_symlink_alias_keep_distinct_contexts():
    """Nested imports through a symlink alias must not overwrite each other's contexts.

    When pkg.mod imports alias.mod (where alias is a symlink to pkg), both modules
    share the same real file. _enter_module_context was keyed by the canonical path,
    so the inner import overwrote the outer context and finishing the inner import
    removed the entry — the outer context leaked open, misattributing subsequent
    coverage. Keying by the raw co_filename (always distinct) keeps both lifecycles
    intact.
    """
    import os
    from pathlib import Path
    import sys
    import tempfile

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as tmpdir:
        tmpdir = Path(os.path.realpath(tmpdir))
        # pkg/ (the real package)
        pkg = tmpdir / "bs_pkg"
        pkg.mkdir()
        (pkg / "__init__.py").write_text("")
        (pkg / "bs_mod.py").write_text("value = 1\n")
        # bs_alias is a symlink to bs_pkg — both names resolve to the same real modules
        os.symlink(pkg, tmpdir / "bs_alias")

        sys.path.insert(0, str(tmpdir))
        try:
            install(include_paths=[tmpdir], collect_import_time_coverage=True)

            # Import the same module through both names — each gets its own context
            import bs_alias.bs_mod  # noqa: F401
            import bs_pkg.bs_mod  # noqa: F401

            # Both contexts should have been entered and finished (no leak)
            collector = ModuleCodeCollector._instance
            assert collector is not None
            assert len(collector._import_time_contexts) == 0, "import-time contexts leaked: %s" % list(
                collector._import_time_contexts.keys()
            )

            # The canonical file should be in _import_time_covered (both imports share it)
            canonical_mod = os.path.realpath(str(pkg / "bs_mod.py"))
            assert canonical_mod in collector._import_time_covered, (
                "import-time coverage missing for %s" % canonical_mod
            )

            # A test context after the nested imports should not be polluted
            with ModuleCodeCollector.CollectInContext() as ctx:
                # Nothing from the import contexts should leak into this test context
                covered = ctx.get_covered_file_paths()
                assert canonical_mod not in covered, (
                    "leaked import context attributed %s to the test context" % canonical_mod
                )
        finally:
            sys.path.remove(str(tmpdir))


@pytest.mark.subprocess()
def test_bitmaps_resolve_symlinked_workspace():
    """Covered paths recorded under canonical paths must match a symlinked workspace.

    When CI workspaces are symlinked (e.g. Jenkins WORKSPACE), the plugin passes
    the alias path as the reporting workspace. Instrumentation records coverage
    under realpath(co_filename). get_coverage_bitmaps must resolve the workspace
    to the canonical path so covered files are not silently dropped.
    """
    import os
    from pathlib import Path
    import sys
    import tempfile

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from ddtrace.testing.internal.tracer_api.coverage import CoverageData

    with tempfile.TemporaryDirectory() as tmpdir:
        real_ws = Path(os.path.realpath(tmpdir)) / "real"
        real_ws.mkdir()
        (real_ws / "bs_mod.py").write_text("value = 1\n")

        alias_ws = Path(tmpdir) / "alias"
        os.symlink(real_ws, alias_ws)

        sys.path.insert(0, str(alias_ws))
        try:
            install(include_paths=[alias_ws], collect_import_time_coverage=True)

            ModuleCodeCollector.start_coverage()
            import bs_mod  # noqa: F401

            ModuleCodeCollector.stop_coverage()

            with ModuleCodeCollector.CollectInContext() as ctx:
                ctx.anchor_file(os.path.realpath(str(alias_ws / "bs_mod.py")))

            # Simulate the reporting path: the plugin passes the alias workspace
            # (as it knows it), not the canonical path. The bitmaps must still
            # include bs_mod.py.
            data = CoverageData()
            data._covered_file_paths = ctx.get_covered_file_paths()
            bitmaps = dict(data.get_coverage_bitmaps(relative_to=alias_ws))
            assert "/bs_mod.py" in bitmaps, (
                "bs_mod.py dropped; workspace alias does not match canonical covered paths: %s" % alias_ws
            )
        finally:
            sys.path.remove(str(alias_ws))


@pytest.mark.subprocess()
def test_reports_include_executable_but_uncovered_files():
    """Files with executable lines but no execution events must not raise KeyError.

    A module imported while the collector is installed but coverage is stopped
    gets an entry in instance.lines but none in the covered data. The report
    entry points previously relied on defaultdict auto-vivification for that
    case; the set conversion must preserve it.
    """
    import contextlib
    import io
    import json
    from pathlib import Path
    import sys
    import tempfile

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as tmpdir:
        ws = Path(tmpdir) / "ws"
        ws.mkdir()
        (ws / "unc_mod.py").write_text("a = 1\nb = 2\n")

        sys.path.insert(0, str(ws))
        try:
            install(include_paths=[ws], collect_import_time_coverage=True)
            # Coverage is stopped: instrumentation records executable lines, but
            # no execution events are collected.
            import unc_mod  # noqa: F401

            # The console report must not raise on the executable-but-uncovered
            # entry (it skips 0%-covered files from its rows by design; the JSON
            # report below asserts the entry itself).
            with contextlib.redirect_stdout(io.StringIO()):
                ModuleCodeCollector.report(ws)

            report_file = Path(tmpdir) / "report.json"
            ModuleCodeCollector.write_json_report_to_file(str(report_file), ws)
            files = json.loads(report_file.read_text())["files"]
            entry = files["/unc_mod.py"] if "/unc_mod.py" in files else files["unc_mod.py"]
            assert entry["executed_lines"] == [], "uncovered module must report no executed lines"
            assert entry["missing_lines"], "uncovered module must report missing lines"
        finally:
            sys.path.remove(str(ws))


@pytest.mark.subprocess()
def test_join_thread_created_before_install():
    """Threads created before install() must survive the patched join.

    _patch_threading patches Thread.join at the class level, so a thread whose
    __init__ predates installation reaches join() without coverage state; the
    CI traceback AttributeError: 'Thread' object has no attribute '_should_cover'
    on join must not recur.
    """
    import threading

    from ddtrace.internal.coverage.installer import install

    t = threading.Thread(target=lambda: None)
    t.start()
    t.join()  # unpatched join: the thread exits before installation

    install(include_paths=None, collect_import_time_coverage=True)

    # Second join on the terminated thread goes through the patched method
    # without _should_cover; it must be a no-op, not an AttributeError.
    t.join()


@pytest.mark.subprocess()
def test_report_and_json_report_resolve_symlinked_workspace():
    """Console and JSON reports must relativize against a symlinked workspace.

    Same scenario as test_bitmaps_resolve_symlinked_workspace: instrumentation
    records under realpath(co_filename), and the report entry points receive the
    workspace alias. report() and write_json_report_to_file() must resolve it, or
    the reports regress to absolute filenames.
    """
    import contextlib
    import io
    import json
    from pathlib import Path
    import sys
    import tempfile

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    with tempfile.TemporaryDirectory() as tmpdir:
        real_ws = Path(tmpdir) / "real"
        real_ws.mkdir()
        (real_ws / "rep_mod.py").write_text("value = 1\n")

        alias_ws = Path(tmpdir) / "alias"
        alias_ws.symlink_to(real_ws)

        sys.path.insert(0, str(alias_ws))
        try:
            install(include_paths=[alias_ws], collect_import_time_coverage=True)

            ModuleCodeCollector.start_coverage()
            import rep_mod  # noqa: F401

            ModuleCodeCollector.stop_coverage()

            # JSON report: filenames must be workspace-relative despite the alias.
            report_file = Path(tmpdir) / "report.json"
            ModuleCodeCollector.write_json_report_to_file(str(report_file), alias_ws)
            files = json.loads(report_file.read_text())["files"]
            assert any(key in ("rep_mod.py", "/rep_mod.py") for key in files), (
                "JSON report regressed to absolute filenames; workspace alias not resolved: %s" % alias_ws
            )
            assert all(not key.startswith(str(alias_ws)) for key in files)

            # Console report: must relativize against the alias too.
            with contextlib.redirect_stdout(io.StringIO()) as buf:
                ModuleCodeCollector.report(alias_ws)
            out = buf.getvalue()
            assert "rep_mod.py" in out, "console report lost the covered module row"
            assert str(alias_ws) not in out, "console report regressed to absolute filenames"
        finally:
            sys.path.remove(str(alias_ws))
