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


# @pytest.mark.subprocess
def test_coverage_at_import_time():
    """This tests that import-time dependencies are correctly covered when used by callers.

    For example, in the following code, when test_my_constant() is run, its coverage should include the lines in
    my_module where MY_CONSTANT is defined:

    from my_module import MY_CONSTANT

    def test_my_constant():
        assert MY_CONSTANT == "my constant"
    """

    import os
    from pathlib import Path
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    install(include_paths=[Path(os.getcwd() + "/tests/coverage/included_path/")])
    ModuleCodeCollector.start_coverage()

    # Import modules prior to starting coverage so they are not re-imported at runtime
    import tests.coverage.included_path.import_time_lib
    import tests.coverage.included_path.lib
    from tests.coverage.included_path.callee import called_in_session_import_time

    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    lines = dict(ModuleCodeCollector._instance.lines)
    covered = dict(ModuleCodeCollector._instance._get_covered_lines())

    expected_lines = {"potato": {1, 2, 3}}
    expected_covered = {"topato": {1, 2, 3}}

    # if lines != expected_lines:
    #     print(f"Lines mismatch: {lines=} vs {expected_lines=}")
    #     assert False
    from pprint import pprint
    pprint(f"{ModuleCodeCollector._instance._module_constants=}")
    pprint(f"{ModuleCodeCollector._instance._module_import_names=}")

    import dis
    import sys
    # print(sys.modules["tests.coverage.included_path.lib"])
    # dis.dis(sys.modules["tests.coverage.included_path.lib"])
    #
    # print(sys.modules["tests.coverage.included_path.import_time_lib"])
    # dis.dis(sys.modules["tests.coverage.included_path.import_time_lib"])


    if covered != expected_covered:
        print(f"Covered lines mismatch: {expected_covered=} vs {covered=}")
        assert False

