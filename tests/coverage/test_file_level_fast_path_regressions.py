import sys

import pytest


def test_file_level_paths_include_line_only_context_coverage():
    """File-level path snapshots must preserve files only present in the line coverage context.

    Python < 3.12 records file-level coverage through line hooks, and multiprocessing child coverage is merged back
    into the parent line dictionary. In both cases, the file-level upload path must include those line-dict keys.
    """
    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.code import _get_ctx_covered_files
    from ddtrace.internal.coverage.code import _get_ctx_covered_lines

    old_instance = ModuleCodeCollector._instance
    collector = ModuleCodeCollector()
    collector._file_level_coverage = True
    ModuleCodeCollector._instance = collector

    try:
        with ModuleCodeCollector.CollectInContext() as context_collector:
            _get_ctx_covered_files().add("/repo/parent.py")
            _get_ctx_covered_lines()["/repo/child.py"].add(0)

            assert context_collector.get_covered_file_paths() == {"/repo/parent.py", "/repo/child.py"}
    finally:
        ModuleCodeCollector._instance = old_instance


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true", "false"]})
def test_same_file_fast_path_preserves_late_dynamic_import_dependency():
    """Guard against skipping same-file PY_START events too aggressively.

    File-level optimizations may want to stop doing work once a file is already
    covered in the current context. That is only safe if later code objects in
    that same file can still publish their import dependency metadata. Here the
    dependency module is imported before the test contexts, so the second
    context only gets that module's import-time coverage if the dynamic import
    dependency edge from ``covered_then_dynamic_import.py`` is preserved.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Imported before the test contexts: later contexts should only include it
    # when dependency tracking proves the executed code imported it.
    from tests.coverage.included_path import preimported_dependency  # noqa:F401
    from tests.coverage.included_path.covered_then_dynamic_import import cover_file_without_import
    from tests.coverage.included_path.covered_then_dynamic_import import import_preimported_dependency

    with ModuleCodeCollector.CollectInContext() as context_without_dynamic_import:
        assert cover_file_without_import() == "covered"
        without_dynamic_import = _get_relpath_dict(cwd_path, context_without_dynamic_import.get_covered_lines())

    with ModuleCodeCollector.CollectInContext() as context_with_dynamic_import:
        # Cover the file first. A too-aggressive file-level fast path would be tempted to suppress the rest of the
        # file's PY_START callbacks after this point.
        assert cover_file_without_import() == "covered"
        assert import_preimported_dependency() == 42
        with_dynamic_import = _get_relpath_dict(cwd_path, context_with_dynamic_import.get_covered_lines())

    covered_file = "tests/coverage/included_path/covered_then_dynamic_import.py"
    dependency_file = "tests/coverage/included_path/preimported_dependency.py"

    assert covered_file in without_dynamic_import
    assert dependency_file not in without_dynamic_import, "import-time coverage should not leak into unrelated contexts"

    assert covered_file in with_dynamic_import
    assert dependency_file in with_dynamic_import, "late dynamic import dependency must merge import-time coverage"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true"]})
def test_file_level_path_fast_path_preserves_late_dynamic_import_dependency():
    """File-level get_covered_file_paths() must include dependency edges discovered after an earlier cache hit.

    The first context covers ``covered_then_dynamic_import.py`` without executing its dynamic import, then asks for
    file paths. That populates the file-level covered-path cache for the singleton covered-file set. The second context
    covers the same file set but also executes the dynamic import. If dynamic import metadata does not invalidate the
    file-level cache, the preimported dependency would be omitted.
    """
    import os
    from pathlib import Path

    def relpath_set(rootpath, paths):
        root = Path(rootpath)
        return {str(Path(path).relative_to(root)) for path in paths}

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Imported before the test contexts: later contexts only include it through import-time dependency expansion.
    from tests.coverage.included_path import preimported_dependency  # noqa:F401
    from tests.coverage.included_path.covered_then_dynamic_import import cover_file_without_import
    from tests.coverage.included_path.covered_then_dynamic_import import import_preimported_dependency

    with ModuleCodeCollector.CollectInContext() as context_without_dynamic_import:
        assert cover_file_without_import() == "covered"
        without_dynamic_import = relpath_set(cwd_path, context_without_dynamic_import.get_covered_file_paths())

    with ModuleCodeCollector.CollectInContext() as context_with_dynamic_import:
        assert cover_file_without_import() == "covered"
        assert import_preimported_dependency() == 42
        with_dynamic_import = relpath_set(cwd_path, context_with_dynamic_import.get_covered_file_paths())

    covered_file = "tests/coverage/included_path/covered_then_dynamic_import.py"
    dependency_file = "tests/coverage/included_path/preimported_dependency.py"

    assert covered_file in without_dynamic_import
    assert dependency_file not in without_dynamic_import, "import-time coverage should not leak into unrelated contexts"

    assert covered_file in with_dynamic_import
    assert dependency_file in with_dynamic_import, "late dynamic import dependency must invalidate cached file paths"


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true"]})
def test_file_level_path_fast_path_preserves_nested_dynamic_import_dependencies():
    """Nested dynamic imports should be represented in file-level path output across contexts."""
    import os
    from pathlib import Path

    def relpath_set(rootpath, paths):
        root = Path(rootpath)
        return {str(Path(path).relative_to(root)) for path in paths}

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.nested_fixture import fixture_dynamic_path
    from tests.coverage.included_path.nested_fixture import fixture_toplevel_path

    with ModuleCodeCollector.CollectInContext() as context_toplevel:
        fixture_toplevel_path(5)
        toplevel_files = relpath_set(cwd_path, context_toplevel.get_covered_file_paths())

    with ModuleCodeCollector.CollectInContext() as context_dynamic:
        fixture_dynamic_path(10)
        dynamic_files = relpath_set(cwd_path, context_dynamic.get_covered_file_paths())

    assert "tests/coverage/included_path/nested_fixture.py" in toplevel_files
    assert "tests/coverage/included_path/layer2_toplevel.py" in toplevel_files
    assert "tests/coverage/included_path/layer2_dynamic.py" not in toplevel_files

    assert "tests/coverage/included_path/nested_fixture.py" in dynamic_files
    assert "tests/coverage/included_path/layer2_dynamic.py" in dynamic_files
    assert "tests/coverage/included_path/layer3_toplevel.py" in dynamic_files
    assert "tests/coverage/included_path/layer3_dynamic.py" in dynamic_files
    assert "tests/coverage/included_path/constants_dynamic.py" in dynamic_files


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true"]})
def test_file_level_import_dependencies_do_not_include_unexecuted_branches():
    """File-level import dependency expansion should only include imports from the branch that ran.

    PY_START fires once for the whole function code object. If file-level mode publishes every import opcode found in
    that code object, the unexecuted branch's pre-imported dependency is incorrectly attributed to this test.
    """
    import os
    from pathlib import Path

    def relpath_set(rootpath, paths):
        root = Path(rootpath)
        return {str(Path(path).relative_to(root)) for path in paths}

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    # Pre-import both dependencies so they are available as import-time coverage. The test context below only executes
    # the branch that imports branch_dep_a, so branch_dep_b must not be attributed to it.
    from tests.coverage.included_path import branch_dep_a  # noqa:F401
    from tests.coverage.included_path import branch_dep_b  # noqa:F401
    from tests.coverage.included_path.branch_import_dependencies import choose_dependency

    with ModuleCodeCollector.CollectInContext() as context:
        assert choose_dependency(True) == "a"
        covered_files = relpath_set(cwd_path, context.get_covered_file_paths())

    assert "tests/coverage/included_path/branch_import_dependencies.py" in covered_files
    assert "tests/coverage/included_path/branch_dep_a.py" in covered_files
    assert "tests/coverage/included_path/branch_dep_b.py" not in covered_files


@pytest.mark.skipif(sys.version_info < (3, 12), reason="Test specific to Python 3.12+ monitoring API")
@pytest.mark.subprocess(parametrize={"_DD_COVERAGE_FILE_LEVEL": ["true"]})
def test_file_level_importlib_import_module_dependency_is_included():
    """File-level dependency expansion should include modules loaded via importlib.import_module.

    Bytecode scanning only sees the importlib import itself, not the target module name string passed at runtime. If the
    target dependency was pre-imported before the context, it is only reported when runtime import calls are tracked.
    """
    import os
    from pathlib import Path

    def relpath_set(rootpath, paths):
        root = Path(rootpath)
        return {str(Path(path).relative_to(root)) for path in paths}

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path import importlib_dep  # noqa:F401
    from tests.coverage.included_path.importlib_dynamic_dependency import load_dependency

    with ModuleCodeCollector.CollectInContext() as context:
        assert load_dependency() == "dynamic"
        covered_files = relpath_set(cwd_path, context.get_covered_file_paths())

    assert "tests/coverage/included_path/importlib_dynamic_dependency.py" in covered_files
    assert "tests/coverage/included_path/importlib_dep.py" in covered_files
