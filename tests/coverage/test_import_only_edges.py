import sys
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize(
    "globals_,name,level,expected",
    [
        ({"__package__": "pkg.child"}, "leaf", 1, "pkg.child.leaf"),
        ({"__package__": None, "__spec__": SimpleNamespace(parent="pkg.child")}, "leaf", 2, "pkg.leaf"),
        ({"__name__": "pkg.child.caller"}, "leaf", 1, "pkg.child.leaf"),
        ({"__name__": "pkg.child", "__path__": []}, "", 1, "pkg.child"),
        ({"__package__": "ignored"}, "absolute.leaf", 0, "absolute.leaf"),
    ],
)
def test_relative_import_package_fallback(globals_, name, level, expected):
    from ddtrace.internal.coverage.import_only import _resolve_absolute_name

    assert _resolve_absolute_name(name, globals_, level) == expected


@pytest.mark.parametrize(
    "source,loaded,expected",
    [
        ("if False:\n    import edge_candidate\n", True, ("edge_candidate",)),
        ("def unused():\n    import edge_candidate\n", True, ("edge_candidate",)),
        ("try:\n    import edge_candidate\nexcept ImportError:\n    pass\n", True, ("edge_candidate",)),
        ("import edge_candidate\n", False, ()),
        ("if False:\n    from ... import edge_candidate\n", True, ()),
        ("not valid Python!", True, ()),
    ],
)
def test_backfill_candidates_are_not_execution_history(tmp_path, monkeypatch, source, loaded, expected):
    from ddtrace.internal.coverage.import_only import ImportOnlyCoverage

    file = tmp_path / "caller.py"
    file.write_text(source)
    monkeypatch.setitem(sys.modules, "edge_candidate", SimpleNamespace() if loaded else None)
    edges = ImportOnlyCoverage._static_import_names("pkg.caller", str(file))
    assert edges == ((("", expected),) if expected else ())


def test_backfill_filter_is_not_memoized(tmp_path, monkeypatch):
    from ddtrace.internal.coverage.import_only import ImportOnlyCoverage

    file = tmp_path / "caller.py"
    file.write_text("import edge_late_target\n")
    monkeypatch.setitem(sys.modules, "edge_late_target", None)
    assert ImportOnlyCoverage._static_import_names("caller", str(file)) == ()
    monkeypatch.setitem(sys.modules, "edge_late_target", SimpleNamespace())
    assert ImportOnlyCoverage._static_import_names("caller", str(file)) == (("", ("edge_late_target",)),)


@pytest.mark.subprocess()
def test_pure_import_metadata_caches_are_bounded_and_cleared():
    from types import SimpleNamespace

    from ddtrace.internal.coverage.import_only import _absolute_name
    from ddtrace.internal.coverage.import_only import _import_names
    from ddtrace.internal.coverage.import_only import _module_name_prefixes
    from ddtrace.internal.coverage.import_only import install_import_hook
    from ddtrace.internal.coverage.import_only import uninstall_import_hook

    recorder = SimpleNamespace(record_import_file=lambda path: None, record_import_edge=lambda *args: None)
    install_import_hook(recorder, lambda path: False)
    try:
        for index in range(4100):
            name = f"pkg.module_{index}"
            _absolute_name(".leaf", name)
            _module_name_prefixes(name)
            assert _import_names(name, ("leaf",)) is _import_names(name, ("leaf",))
        for cached in (_absolute_name, _module_name_prefixes, _import_names):
            assert cached.cache_info().currsize == 4096
    finally:
        uninstall_import_hook()
    for cached in (_absolute_name, _module_name_prefixes, _import_names):
        assert cached.cache_info().currsize == 0


@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_backfill_aliases_preserve_package_and_parse_once():
    import ast
    import importlib
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        package = root / "edge_alias_pkg"
        package.mkdir()
        (package / "__init__.py").write_text("from . import leaf\n")
        (package / "leaf.py").write_text("value = 1\n")
        sys.path.insert(0, str(root))
        module = importlib.import_module("edge_alias_pkg")
        sys.modules["unrelated.alias"] = module
        with patch.object(ast, "parse", wraps=ast.parse) as parse:
            ModuleCodeCollector.install(include_paths=[root])
        assert parse.call_count == 2, "aliases must not parse the same source again"
        collector = ModuleCodeCollector._instance
        assert collector._import_time_name_to_path["unrelated.alias"] == str(package / "__init__.py")
        with ModuleCodeCollector.CollectInContext(test_file=package / "__init__.py") as context:
            pass
        assert str(package / "leaf.py") in context.get_covered_file_paths()


@pytest.mark.subprocess(env={"_DD_COVERAGE_IMPORT_ONLY": "1"})
def test_import_metadata_does_not_expand_per_test_coverage():
    import importlib
    from pathlib import Path
    import sys
    from tempfile import TemporaryDirectory
    from unittest.mock import patch

    from ddtrace.internal.coverage.code import ModuleCodeCollector

    with TemporaryDirectory() as directory:
        root = Path(directory).resolve()
        (root / "edge_raw_import.py").write_text("value = 42\n")
        sys.path.insert(0, str(root))
        ModuleCodeCollector.install(include_paths=[root])
        mode = ModuleCodeCollector._instance._mode
        with patch.object(mode, "expand_context_lines", side_effect=AssertionError("per-test expansion during import")):
            module = importlib.import_module("edge_raw_import")
        assert module.value == 42
        assert str(root / "edge_raw_import.py") in ModuleCodeCollector._instance._import_time_covered
