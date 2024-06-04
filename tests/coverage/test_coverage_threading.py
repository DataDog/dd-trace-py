import pytest


@pytest.mark.subprocess
def test_coverage_threading_session():
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    cwd = os.getcwd()

    include_paths = [Path(cwd) / "tests/coverage/sample_code/"]
    ModuleCodeCollector.install(include_paths=include_paths)

    ModuleCodeCollector.start_coverage()
    from tests.coverage.sample_code.threading.caller import call_add_in_thread

    call_add_in_thread()
    ModuleCodeCollector.stop_coverage()

    covered_lines = dict(ModuleCodeCollector._instance._get_covered_lines())

    expected_lines = {
        f"{cwd}/tests/coverage/sample_code/threading/caller.py": {1, 4, 5, 7, 8, 9, 11},
        f"{cwd}/tests/coverage/sample_code/threading/callee.py": {1, 2, 3, 5, 6, 8, 15},
        f"{cwd}/tests/coverage/sample_code/threading/lib.py": {1, 2, 4},
    }

    if expected_lines != covered_lines:
        print(f"Mismatched lines: {expected_lines=} vs  {covered_lines=}")
        assert False


@pytest.mark.subprocess
def test_coverage_threading_context():
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    cwd = os.getcwd()

    include_paths = [Path(cwd) / "tests/coverage/sample_code/"]
    ModuleCodeCollector.install(include_paths=include_paths)
    from tests.coverage.sample_code.threading.caller import call_add_in_thread
    call_add_in_thread()
    with ModuleCodeCollector.CollectInContext() as context_collector:
        from tests.coverage.sample_code.threading.caller import call_add_in_thread_context
        call_add_in_thread_context()
    context_covered = dict(context_collector.get_covered_lines())

    expected_lines = { }
    if expected_lines != context_covered:
        print(f"Mismatched lines: {expected_lines=} vs  {context_covered=}")
        assert False

@pytest.mark.subprocess
def test_coverage_threading_session_and_context():
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from tests.coverage.sample_code.threading.caller import call_add_in_thread

    ModuleCodeCollector.install(include_paths=[Path(__file__).parent / "sample_code"])
    ModuleCodeCollector.start_coverage()
    with ModuleCodeCollector.CollectInContext() as collector:
        call_add_in_thread()
    context_covered = ModuleCodeCollector._instance._get_covered_lines()
    ModuleCodeCollector.stop_coverage()
