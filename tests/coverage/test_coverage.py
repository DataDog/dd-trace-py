import pytest


@pytest.mark.subprocess
def test_coverage_nested_contexts():
    import os
    from pathlib import Path
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    install(include_paths=[Path(os.getcwd() + "/tests/coverage/included_path/")])

    with ModuleCodeCollector.CollectInContext() as outer_collector:
        from tests.coverage.included_path.callee import called_in_context_main
        called_in_context_main(1, 2)

        print(f"Outer collector lines: {outer_collector.get_covered_lines()}")

        with ModuleCodeCollector.CollectInContext() as inner_collector:
            from tests.coverage.included_path.callee import called_in_context_nested
            called_in_context_nested(1, 2)

        print(f"Inner collector lines: {inner_collector.get_covered_lines()}")

    inner_lines = inner_collector.get_covered_lines()
    outer_lines = outer_collector.get_covered_lines()
    assert inner_lines == {} , f"Inner collector lines mismatch: {inner_lines=} {outer_lines=}"
    assert outer_lines == {} , f"Outer collector lines mismatch: {outer_lines}"
