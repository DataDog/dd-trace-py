"""Tests for import-only coverage mode (`_DD_COVERAGE_IMPORT_ONLY`).

Import-only mode collects file-level coverage exclusively from import events:
wrappers around `builtins.__import__` and `importlib.import_module` replace all
code instrumentation (no sys.monitoring events, no bytecode rewriting). These tests
encode the semantics validated by the import-only POC:

- the motivating concern example: a module-level import attributed to both contexts,
  a shared function-local import attributed to both (including the sys.modules cache
  hit on the second execution), and per-function imports isolated to their context
- import-time dependency closure expansion from the covered importing file
- runtime-false guarded imports do not create dependency edges
- site-packages paths are never recorded
- modules imported via importlib.import_module (which bypasses builtins.__import__)
  are still recorded when a coverage window is open
"""

import sys

import pytest


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Test requires Python 3.9+")
@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_concern_example():
    """The motivating concern example, with per-context attribution."""
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.io_test_module import io_test_x
    from tests.coverage.included_path.io_test_module import io_test_z

    with ModuleCodeCollector.CollectInContext() as context_z:
        io_test_z()
        covered_z = _get_relpath_dict(cwd_path, context_z.get_covered_lines())

    with ModuleCodeCollector.CollectInContext() as context_x:
        io_test_x()
        covered_x = _get_relpath_dict(cwd_path, context_x.get_covered_lines())

    expected_common = {
        "tests/coverage/included_path/io_test_module.py",
        "tests/coverage/included_path/io_mod_abc.py",
        "tests/coverage/included_path/io_mod_xyz.py",
    }
    # Module-level import (io_mod_abc) and the shared function-local import
    # (io_mod_xyz, a cache hit on the second execution) are attributed to both.
    assert expected_common <= set(covered_z), "test_z missing expected files: %s" % (expected_common - set(covered_z))
    assert expected_common <= set(covered_x), "test_x missing expected files: %s" % (expected_common - set(covered_x))
    # Per-function imports are isolated to their own context.
    assert "tests/coverage/included_path/io_mod_edf.py" in covered_z
    assert "tests/coverage/included_path/io_mod_edf.py" not in covered_x
    assert "tests/coverage/included_path/io_mod_hjk.py" in covered_x
    assert "tests/coverage/included_path/io_mod_hjk.py" not in covered_z


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Test requires Python 3.9+")
@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_global_window_and_importlib():
    """Modules imported during a coverage window are covered, even via importlib."""
    import importlib
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    ModuleCodeCollector.start_coverage()
    importlib.import_module("tests.coverage.included_path.io_mod_hjk")
    from tests.coverage.included_path.io_test_module import io_test_z  # noqa: F401

    io_test_z()
    ModuleCodeCollector.stop_coverage()

    covered = _get_relpath_dict(cwd_path, ModuleCodeCollector._instance._get_covered_lines(include_imported=True))

    # importlib.import_module has a separate wrapper because it bypasses __import__.
    assert "tests/coverage/included_path/io_mod_hjk.py" in covered
    # The import statement in io_test_z's body records the caller (executable-lines
    # sentinel, closure root) and the imported files.
    assert "tests/coverage/included_path/io_test_module.py" in covered
    assert "tests/coverage/included_path/io_mod_abc.py" in covered
    assert "tests/coverage/included_path/io_mod_edf.py" in covered


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Test requires Python 3.9+")
@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_false_guarded_import_not_tracked():
    """Runtime-false import statements do not create dependency edges.

    Note: io_guarded_caller() executes no import statement, so the context attributes
    nothing to it — that is the import-only semantic (execution without an import
    statement is not observable). The guarded-import guarantee is checked on the
    dependency graph itself.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install
    from tests.coverage.utils import _get_relpath_dict

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.io_guarded_import import io_guarded_caller

    # No import statement executes in this window: nothing is attributed.
    with ModuleCodeCollector.CollectInContext() as context:
        io_guarded_caller()
        covered = _get_relpath_dict(cwd_path, context.get_covered_lines())

    assert covered == {}, "a context whose code executes no import statement records nothing"

    collector = ModuleCodeCollector._instance
    guarded_path = str(Path(cwd_path + "/tests/coverage/included_path/io_guarded_import.py"))

    # The runtime-false guarded import did not create a dependency edge.
    guarded_names = {
        name for _package, modules in collector._import_names_by_path.get(guarded_path, set()) for name in modules
    }
    assert any("io_mod_xyz" in name for name in guarded_names), "the unguarded import edge must be recorded"
    assert not any("io_mod_edf" in name for name in guarded_names), "the guarded import edge must not exist"

    # And the import-time closure of the guarded module reflects the same.
    closure = collector._get_import_time_dependency_paths(guarded_path)
    assert guarded_path in closure
    assert any("io_mod_xyz" in path for path in closure)
    assert not any("io_mod_edf" in path for path in closure)


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Test requires Python 3.9+")
@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1", "PACKAGES_DIRNAME": "site-packages"})
def test_import_only_site_packages_paths_are_not_recorded():
    """Files under site-packages/dist-packages inside the workspace are never recorded."""
    import importlib
    import os
    from pathlib import Path
    import sys
    import tempfile
    import uuid

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    module_suffix = uuid.uuid4().hex

    with tempfile.TemporaryDirectory() as tmpdir:
        # Resolve the tmpdir: on macOS the import machinery realpaths module
        # co_filenames (/var/folders -> /private/var/...), so an unresolved include
        # path never matches and nothing is recorded.
        workspace_path = Path(tmpdir).resolve()
        packages_dirname = os.environ["PACKAGES_DIRNAME"]
        app_module_name = f"app_module_{module_suffix}"
        vendor_module_name = f"vendor_module_{module_suffix}"
        app_module_path = workspace_path / f"{app_module_name}.py"
        packages_path = workspace_path / "nested" / packages_dirname
        vendor_module_path = packages_path / f"{vendor_module_name}.py"

        app_module_path.write_text("from %s import value\nvalue = value + 1\n" % vendor_module_name)
        packages_path.mkdir(parents=True)
        vendor_module_path.write_text("value = 1\n")

        sys.path.insert(0, str(packages_path))
        sys.path.insert(0, str(workspace_path))
        try:
            install(include_paths=[workspace_path], collect_import_time_coverage=True)

            ModuleCodeCollector.start_coverage()
            importlib.import_module(app_module_name)
            ModuleCodeCollector.stop_coverage()
        finally:
            sys.path.remove(str(workspace_path))
            sys.path.remove(str(packages_path))

        app_module_path_str = os.path.realpath(app_module_path)
        vendor_module_path_str = os.path.realpath(vendor_module_path)
        collector = ModuleCodeCollector._instance
        assert collector is not None

        assert app_module_path_str in collector.lines
        assert app_module_path_str in collector._get_covered_lines(include_imported=True)
        assert vendor_module_path_str not in collector.lines
        assert vendor_module_path_str not in collector._get_covered_lines(include_imported=True)
        assert vendor_module_path_str not in collector._import_time_name_to_path.values()


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Test requires Python 3.9+")
@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_anchor_attributes_test_file_closure():
    """Anchoring a test file attributes it plus its import-time closure, with no
    import statement executing inside the coverage window.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.installer import install
    from ddtrace.testing.internal.tracer_api.coverage import coverage_collection

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.io_test_module import io_test_z  # noqa: F401

    anchor = Path(cwd_path + "/tests/coverage/included_path/io_test_module.py")
    with coverage_collection(anchor_paths=[anchor]) as coverage_data:
        # Execute nothing: the anchor alone must attribute the file and its closure.
        pass

    covered = {str(Path(p).relative_to(cwd_path)) for p in coverage_data._covered_file_paths}
    assert "tests/coverage/included_path/io_test_module.py" in covered
    assert "tests/coverage/included_path/io_mod_abc.py" in covered, "module-level import closure"


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Test requires Python 3.9+")
@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_session_level_import_edges_expand_from_anchor():
    """Import statements executed outside any coverage window (session-level lazy
    imports) still become import-time edges, so anchored closures expand them.
    """
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.installer import install
    from ddtrace.testing.internal.tracer_api.coverage import coverage_collection

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    install(include_paths=[include_path], collect_import_time_coverage=True)

    from tests.coverage.included_path.io_session_import import io_session_importer

    # Session-level import: executes the lazy import OUTSIDE any coverage window.
    assert io_session_importer() == 10

    anchor = Path(cwd_path + "/tests/coverage/included_path/io_session_import.py")
    with coverage_collection(anchor_paths=[anchor]) as coverage_data:
        pass

    covered = {str(Path(p).relative_to(cwd_path)) for p in coverage_data._covered_file_paths}
    assert "tests/coverage/included_path/io_session_import.py" in covered
    assert "tests/coverage/included_path/io_mod_hjk.py" in covered, (
        "the out-of-window lazy import edge must expand from the anchor"
    )


@pytest.mark.skipif(sys.version_info < (3, 9), reason="Test requires Python 3.9+")
@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_uninstall_restores_import():
    """Uninstalling the collector restores builtins.__import__."""
    import builtins
    import os
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector
    from ddtrace.internal.coverage.installer import install

    cwd_path = os.getcwd()
    include_path = Path(cwd_path + "/tests/coverage/included_path/")

    original_import = builtins.__import__
    install(include_paths=[include_path], collect_import_time_coverage=True)
    assert builtins.__import__ is not original_import

    ModuleCodeCollector.uninstall()
    assert builtins.__import__ is original_import

    # Imports still work after uninstall.
    import json  # noqa: F401


@pytest.mark.subprocess()
def test_import_only_hooks_survive_other_wrappers_and_reinstallation():
    import builtins
    import importlib
    from types import SimpleNamespace

    from ddtrace.internal.coverage.import_hook_instrumentation import install_import_hook
    from ddtrace.internal.coverage.import_hook_instrumentation import uninstall_import_hook

    original_import = builtins.__import__
    original_import_module = importlib.import_module
    events = []
    collector = SimpleNamespace(record_import_file=events.append, record_import_edge=lambda *args: None)
    install_import_hook(collector, lambda path: True)
    captured_import = builtins.__import__
    captured_import_module = importlib.import_module

    def outer_import(*args, **kwargs):
        return captured_import(*args, **kwargs)

    def outer_import_module(*args, **kwargs):
        return captured_import_module(*args, **kwargs)

    builtins.__import__ = outer_import
    importlib.import_module = outer_import_module
    try:
        uninstall_import_hook()
        events.clear()
        assert builtins.__import__("json") is importlib.import_module("json")
        assert events == [], "uninstalled hooks retained by another wrapper must be inert"

        install_import_hook(collector, lambda path: True)
        assert builtins.__import__("json") is importlib.import_module("json")
        assert events, "the new installation must record imports without recursing into itself"
        uninstall_import_hook()
        assert builtins.__import__ is outer_import
        assert importlib.import_module is outer_import_module
        events.clear()
        assert builtins.__import__("json") is importlib.import_module("json")
        assert events == []
    finally:
        builtins.__import__ = original_import
        importlib.import_module = original_import_module
        uninstall_import_hook()


@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_preloaded_package_relative_imports():
    import importlib
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        package = root / "io_preloaded"
        package.mkdir()
        # Reading bytes lets ast.parse honor the same source encoding as Python.
        (package / "__init__.py").write_bytes(b"# coding: latin-1\nfrom . import module_\xe9\n")
        (package / "module_\u00e9.py").write_text("from . import leaf\n")
        (package / "leaf.py").write_text("value = 1\n")
        sys.path.insert(0, str(root))
        importlib.import_module("io_preloaded")
        ModuleCodeCollector.install(include_paths=[root])
        with ModuleCodeCollector.CollectInContext() as context:
            context.anchor_file(str(package / "__init__.py"))
        assert set(context.get_covered_file_paths()) == {
            str(package / "__init__.py"),
            str(package / "module_\u00e9.py"),
            str(package / "leaf.py"),
        }


@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_absolute_import_does_not_resolve_package_shadow():
    import importlib
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        package = root / "io_outer"
        target = root / "io_target"
        package.mkdir()
        target.mkdir()
        (package / "__init__.py").write_text("")
        (package / "io_target.py").write_text("value = 'shadow'\n")
        (package / "caller.py").write_text("import io_target.leaf\n")
        (target / "__init__.py").write_text("from . import dependency\n")
        (target / "leaf.py").write_text("value = 1\n")
        (target / "dependency.py").write_text("value = 2\n")
        sys.path.insert(0, str(root))
        ModuleCodeCollector.install(include_paths=[root])
        importlib.import_module("io_outer.io_target")
        importlib.import_module("io_outer.caller")
        with ModuleCodeCollector.CollectInContext() as context:
            context.anchor_file(str(package / "caller.py"))
        covered = context.get_covered_file_paths()
        assert str(package / "io_target.py") not in covered
        assert {str(target / filename) for filename in ("__init__.py", "leaf.py", "dependency.py")} <= covered


@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_expired_context_does_not_publish_session_edges():
    from contextvars import copy_context
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    root = Path.cwd() / "tests/coverage/included_path"
    ModuleCodeCollector.install(include_paths=[root])
    from tests.coverage.included_path.io_session_import import io_session_importer

    with ModuleCodeCollector.CollectInContext():
        expired = copy_context()
    expired.run(io_session_importer)
    with ModuleCodeCollector.CollectInContext() as context:
        context.anchor_file(str(root / "io_session_import.py"))
    assert str(root / "io_mod_hjk.py") not in context.get_covered_file_paths()


@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_captured_importlib_first_load_is_covered():
    from importlib import import_module
    from pathlib import Path

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    root = Path.cwd() / "tests/coverage/included_path"
    ModuleCodeCollector.install(include_paths=[root])
    with ModuleCodeCollector.CollectInContext() as context:
        import_module("tests.coverage.included_path.io_mod_hjk")
    assert str(root / "io_mod_hjk.py") in context.get_covered_file_paths()


@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_overlapping_roots_expand_context_edges_and_cycles():
    import importlib
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        (root / "io_root.py").write_text("import io_shared\n")
        (root / "io_shared.py").write_text("def load():\n    import io_dynamic\n")
        (root / "io_dynamic.py").write_text("import io_root\nimport io_leaf\n")
        (root / "io_leaf.py").write_text("value = 1\n")
        sys.path.insert(0, str(root))
        ModuleCodeCollector.install(include_paths=[root])
        module = importlib.import_module("io_root")
        with ModuleCodeCollector.CollectInContext() as context:
            context.anchor_file(str(root / "io_root.py"))
            context.anchor_file(str(root / "io_shared.py"))
            module.io_shared.load()
        expected = {str(root / f"{name}.py") for name in ("io_root", "io_shared", "io_dynamic", "io_leaf")}
        assert context.get_covered_file_paths() == expected
        assert set(context.get_covered_lines()) == expected
        with ModuleCodeCollector.CollectInContext() as unrelated:
            unrelated.anchor_file(str(root / "io_root.py"))
        assert unrelated.get_covered_file_paths() == {str(root / "io_root.py"), str(root / "io_shared.py")}


@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_only_new_module_alias_invalidates_cached_resolution():
    from ddtrace.internal.coverage.code import ModuleCodeCollector

    collector = ModuleCodeCollector()
    module_path = "/workspace/already_loaded.py"
    collector._record_import_time_sentinel(module_path)
    collector._register_import_time_name("already_loaded", module_path)
    dependency = ("", ("new_alias",))
    assert collector._get_dependency_paths_for_import_name(dependency) == frozenset()
    # Backfill can discover an alias without adding a new file or import edge.
    collector._register_import_time_name("new_alias", module_path)
    assert collector._get_dependency_paths_for_import_name(dependency) == frozenset({module_path})
