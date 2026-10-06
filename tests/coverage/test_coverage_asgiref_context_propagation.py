"""Regression tests for per-context coverage stack corruption caused by
value-based context restoration.

Django's async test support (asgiref async_to_sync/sync_to_async)
runs a new event loop in a worker thread, which ddtrace's threading
integration wraps in its own coverage context. The framework then restores
context variable values between the caller, the worker thread and the task
using value-based comparisons (cvar.get() != cvalue in asgiref's
_restore_context).

Because the per-context coverage stacks used to be plain lists (compared by
value), such restores could replace one context's stack with another context's
stack object. That corrupted the push/pop pairing of CollectInContext, crashing
the caller with IndexError: pop from empty list and mis-attributing coverage
data between contexts.
"""

import importlib.util

import pytest


# The first test hand-rolls the context-propagation mechanism and has no
# dependency on asgiref. The second exercises the real library and is skipped
# when it is not installed.
HAS_ASGIREF = importlib.util.find_spec("asgiref") is not None


@pytest.mark.subprocess(env={"_DD_COVERAGE_FILE_LEVEL": "false"})
def test_coverage_context_thread_value_based_context_restore():
    import contextvars
    import os
    from pathlib import Path
    import threading

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd = os.getcwd()

    include_paths = [Path(cwd) / "tests/coverage/included_path/"]
    install(include_paths=include_paths)

    # Import before entering the context so module-level lines are not included
    from tests.coverage.included_path.callee import called_in_context_main

    def restore_context_values(context):
        # Mirrors asgiref.sync._restore_context (and similar value-based
        # ContextVar propagation helpers): a restore is skipped whenever the
        # current and incoming values compare equal, and applied otherwise.
        for cvar in context:
            cvalue = context.get(cvar)
            try:
                if cvar.get() != cvalue:
                    cvar.set(cvalue)
            except LookupError:
                cvar.set(cvalue)

    context_collector = ModuleCodeCollector.CollectInContext()
    context_collector.__enter__()
    try:
        # The caller's context, captured while the per-test coverage context
        # is active (fresh empty coverage entries, as at the start of a test).
        caller_context = contextvars.copy_context()

        task_context_holder = {}

        def thread_body():
            # ddtrace's threading integration wraps this thread in its own
            # coverage context, entered in the thread's fresh base context.
            # Simulate the framework task inheriting the thread's context and
            # the caller's context values being restored into it.
            task_context = contextvars.copy_context()

            def task_body():
                restore_context_values(caller_context)
                called_in_context_main(1, 2)

            task_context.run(task_body)
            task_context_holder["task"] = task_context

        thread = threading.Thread(target=thread_body)
        thread.start()
        thread.join()

        # Simulate the framework restoring the task context back into the
        # caller (as asgiref's AsyncToSync does at the end of an async call).
        restore_context_values(task_context_holder["task"])

        context_covered = _get_relpath_dict(cwd, context_collector.get_covered_lines())
    finally:
        # Regression: this used to raise IndexError: pop from empty list
        # (or list index out of range) after the value-based restore
        # replaced this context's stack with the worker thread's (already
        # popped) stack.
        context_collector.__exit__()

    expected_lines = {
        "tests/coverage/included_path/callee.py": {10, 11, 13, 14},
        "tests/coverage/included_path/in_context_lib.py": {1, 2, 5},
    }

    assert expected_lines == context_covered, f"Mismatched lines: {expected_lines} vs  {context_covered}"


@pytest.mark.skipif(not HAS_ASGIREF, reason="asgiref is not installed")
@pytest.mark.subprocess(env={"_DD_COVERAGE_FILE_LEVEL": "false"})
def test_coverage_context_thread_async_to_sync():
    import os
    from pathlib import Path

    from asgiref.sync import async_to_sync
    from asgiref.sync import sync_to_async

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd = os.getcwd()

    include_paths = [Path(cwd) / "tests/coverage/included_path/"]
    install(include_paths=include_paths)

    # Import before entering the context so module-level lines are not included
    from tests.coverage.included_path.callee import called_in_context_main

    def sync_fn(a, b):
        called_in_context_main(a, b)

    async def async_fn(a, b):
        # Simulate a Django async test body performing sync work via
        # sync_to_async (thread-sensitive mode uses the CurrentThreadExecutor
        # in the calling thread).
        await sync_to_async(sync_fn)(a, b)

    context_collector = ModuleCodeCollector.CollectInContext()
    context_collector.__enter__()
    try:
        async_to_sync(async_fn)(1, 2)
        context_covered = _get_relpath_dict(cwd, context_collector.get_covered_lines())
    finally:
        # Regression: this used to raise IndexError: pop from empty list
        # after asgiref restored a value-equal (but different) coverage stack
        # into this context.
        context_collector.__exit__()

    expected_lines = {
        "tests/coverage/included_path/callee.py": {10, 11, 13, 14},
        "tests/coverage/included_path/in_context_lib.py": {1, 2, 5},
    }

    assert expected_lines == context_covered, f"Mismatched lines: {expected_lines} vs  {context_covered}"
