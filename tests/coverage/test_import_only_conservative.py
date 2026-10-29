from collections import defaultdict
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "value,expected",
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", False),
        ("on", False),
        ("0", False),
        ("false", False),
        ("", False),
    ],
)
def test_conservative_boolean_configuration(monkeypatch, value, expected):
    from ddtrace.internal.coverage.import_only import ImportOnlyCoverage

    monkeypatch.setenv("_DD_COVERAGE_IMPORT_ONLY_CONSERVATIVE", value)
    mode = ImportOnlyCoverage(SimpleNamespace())
    assert mode._conservative is expected


def test_conservative_snapshot_cache_and_invalidation(monkeypatch):
    from ddtrace.internal.coverage.coverage_lines import CoverageLines
    from ddtrace.internal.coverage.import_only import ImportOnlyCoverage

    monkeypatch.setenv("_DD_COVERAGE_IMPORT_ONLY_CONSERVATIVE", "1")
    files = defaultdict(CoverageLines)
    collector = SimpleNamespace(_import_time_covered=files)
    mode = ImportOnlyCoverage(collector)
    empty = mode.context_dependency_paths((), {})
    assert empty == frozenset()
    files["/workspace/first.py"].add(0)
    mode.on_import_metadata_changed()
    first = mode.context_dependency_paths((), {})
    assert first == {"/workspace/first.py"}
    assert mode.context_dependency_paths((), {}) is first

    # A new shared edge does not change which files have been seen.
    mode._add_import_time_name("/workspace/first.py", ("", ("new_alias",)))
    assert mode.context_dependency_paths((), {}) is first
    files["/workspace/late.py"].add(0)
    mode.on_import_metadata_changed()
    second = mode.context_dependency_paths((), {})
    assert second == {"/workspace/first.py", "/workspace/late.py"}
    assert first == {"/workspace/first.py"}, "previous snapshots must remain immutable"
    assert empty == frozenset()

    def unexpected_walk(*args):
        raise AssertionError("conservative expansion must not walk per-root closures")

    monkeypatch.setattr(mode, "static_dependency_paths", unexpected_walk)
    assert mode.expand_context_files({"/workspace/injected.py"}, {}) == second | {"/workspace/injected.py"}
    lines = defaultdict(CoverageLines)
    mode.expand_context_lines(lines, {})
    assert set(lines) == second
    assert all(value.to_sorted_list() == [0] for value in lines.values())


@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1", "_DD_COVERAGE_IMPORT_ONLY_CONSERVATIVE": "1"})
def test_conservative_preloaded_callbacks_unused_files_and_late_imports():
    import importlib
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        (root / "conservative_callback.py").write_text("def compute():\n    return 42\n")
        (root / "conservative_unused.py").write_text("value = 1\n")
        (root / "conservative_late.py").write_text("value = 2\n")
        # Never imported: even conservative mode cannot observe every workspace file.
        (root / "conservative_unseen.py").write_text("value = 3\n")
        sys.path.insert(0, str(root))
        callback = importlib.import_module("conservative_callback").compute
        importlib.import_module("conservative_unused")
        ModuleCodeCollector.install(include_paths=[root])
        with ModuleCodeCollector.CollectInContext() as first:
            assert callback() == 42
        paths = first.get_covered_file_paths()
        assert paths == {str(root / "conservative_callback.py"), str(root / "conservative_unused.py")}
        assert set(first.get_covered_lines()) == paths
        # Observed files remain dependencies even if removed from sys.modules.
        del sys.modules["conservative_callback"]
        with ModuleCodeCollector.CollectInContext() as second:
            importlib.import_module("conservative_late")
        assert second.get_covered_file_paths() == paths | {str(root / "conservative_late.py")}
        assert str(root / "conservative_late.py") not in paths
        assert str(root / "conservative_unseen.py") not in second.get_covered_file_paths()
        ModuleCodeCollector.uninstall()
        ModuleCodeCollector.install(include_paths=[root])
        with ModuleCodeCollector.CollectInContext() as fresh:
            pass
        assert str(root / "conservative_callback.py") not in fresh.get_covered_file_paths()
