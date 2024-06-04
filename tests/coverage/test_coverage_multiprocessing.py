import pytest


@pytest.mark.subprocess
def test_coverage_multiprocessing_fork_session():
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from tests.coverage.sample_code.multiprocessing.caller import call_fork

    ModuleCodeCollector.install(include_paths=[Path(__file__).parent / "sample_code"])
    ModuleCodeCollector.start_coverage()
    call_fork()
    ModuleCodeCollector.stop_coverage()
    covered_lines = ModuleCodeCollector._instance._get_covered_lines()
    assert covered_lines == {"hi": "world"}


@pytest.mark.subprocess
def test_coverage_multiprocessing_fork_context():
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from tests.coverage.sample_code.multiprocessing.caller import call_fork

    ModuleCodeCollector.install(include_paths=[Path(__file__).parent / "sample_code"])
    with ModuleCodeCollector.CollectInContext() as collector:
        call_fork()
    context_covered = ModuleCodeCollector._instance._get_covered_lines()


@pytest.mark.subprocess
def test_coverage_multiprocessing_fork_session_and_context():
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from tests.coverage.sample_code.multiprocessing.caller import call_fork

    ModuleCodeCollector.install(include_paths=[Path(__file__).parent / "sample_code"])
    ModuleCodeCollector.start_coverage()
    with ModuleCodeCollector.CollectInContext():
        call_fork()
        context_covered = ModuleCodeCollector._instance._get_covered_lines()
    ModuleCodeCollector.stop_coverage()
    covered_lines = ModuleCodeCollector._instance._get_covered_lines()
