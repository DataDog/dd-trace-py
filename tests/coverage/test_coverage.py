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
    assert inner_lines == {}, f"Inner collector lines mismatch: {inner_lines=} {outer_lines=}"
    assert outer_lines == {}, f"Outer collector lines mismatch: {outer_lines}"


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

    install(include_paths=[Path(os.getcwd() + "/tests/coverage/included_path/")], collect_module_dependencies=True)

    # Import modules prior to starting coverage so they are not re-imported at runtime
    from tests.coverage.included_path.callee import called_in_session_import_time

    ModuleCodeCollector.start_coverage()

    called_in_session_import_time()
    ModuleCodeCollector.stop_coverage()

    from pprint import pprint

    print("\n\nMODULE DEPENDENCIES:\n")
    pprint(ModuleCodeCollector._instance._module_dependencies)
    print("\n\nMODULE NAMES TO FILES:\n")
    pprint(ModuleCodeCollector._instance._module_names_to_files)
    print("\n\nMODULE LEVEL COVERED:\n")
    pprint(ModuleCodeCollector._instance._module_level_covered)
    print("\n\n")

    lines = dict(ModuleCodeCollector._instance.lines)
    covered = dict(ModuleCodeCollector._instance._get_covered_lines(include_imported=False))
    covered_with_imports = dict(ModuleCodeCollector._instance._get_covered_lines(include_imported=True))

    expected_lines = {"potato": {1, 2, 3}}
    expected_covered = {"topato": {1, 2, 3}}

    pass_test = True

    if lines != expected_lines:
        # print(f"Lines mismatch: {lines=} vs {expected_lines=}")
        pass_test = False

    if covered != expected_covered:
        # print(f"Covered lines mismatch: {expected_covered=} vs {covered=}")
        # print(f"Covered lines with imports mismatch: {expected_covered=} vs {covered_with_imports=}")
        pass_test = False

    import pprint
    print(f"Covered lines vs covered with imports:")
    pprint.pprint(covered)
    print(f"\n\nvs\n\n")
    pprint.pprint(covered_with_imports)

    assert pass_test
