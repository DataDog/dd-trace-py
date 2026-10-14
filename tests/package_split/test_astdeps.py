"""Static import analysis tests: edges, parents, guards, dynamic imports, cycles."""

from __future__ import annotations

from pathlib import Path

from package_split.astdeps import build_graph
from package_split.astdeps import find_cycles


def make_tree(tmp_path: Path, files: dict[str, str]) -> Path:
    for relative, content in files.items():
        path = tmp_path / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    return tmp_path


def test_static_and_from_import_edges(tmp_path):
    make_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "import pkg.b\nfrom pkg import c\nfrom pkg.c import helper\n",
            "pkg/b.py": "helper = 1\n",
            "pkg/c.py": "helper = 2\n",
            "pkg/sub/__init__.py": "",
            "pkg/sub/d.py": "from ..b import helper\n",
        },
    )
    graph = build_graph(tmp_path, [tmp_path / "pkg"], ["pkg"])
    edges = {(e.importer, e.imported) for e in graph.edges}

    assert ("pkg.a", "pkg.b") in edges  # import pkg.b
    assert ("pkg.a", "pkg") in edges  # parent-init expansion
    assert ("pkg.a", "pkg.c") in edges  # from pkg import c (submodule)
    assert ("pkg.sub.d", "pkg.b") in edges  # relative from ..b import helper
    assert ("pkg.sub.d", "pkg") in edges  # parent-init expansion of the ..b target


def test_relative_import_in_package_init(tmp_path):
    # Inside pkg/sub/__init__.py, `from . import d` refers to pkg.sub.d,
    # not pkg.d (the importing module *is* the package).
    make_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/sub/__init__.py": "from . import d\n",
            "pkg/sub/d.py": "x = 1\n",
        },
    )
    graph = build_graph(tmp_path, [tmp_path / "pkg"], ["pkg"])
    edges = {(e.importer, e.imported) for e in graph.edges}
    assert ("pkg.sub", "pkg.sub.d") in edges


def test_guarded_imports_are_flagged_not_hidden(tmp_path):
    make_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "try:\n    import pkg.b\nexcept ImportError:\n    pass\n",
            "pkg/b.py": "",
        },
    )
    graph = build_graph(tmp_path, [tmp_path / "pkg"], ["pkg"])
    edge = next(e for e in graph.edges if e.importer == "pkg.a" and e.imported == "pkg.b")
    assert edge.guarded is True


def test_dynamic_imports_literal_and_unresolved(tmp_path):
    make_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": ("import importlib\nimportlib.import_module('pkg.b')\nmod = __import__(name)\n"),
            "pkg/b.py": "",
        },
    )
    graph = build_graph(tmp_path, [tmp_path / "pkg"], ["pkg"])
    resolved = [d for d in graph.dynamic_imports if d.resolved is not None]
    unresolved = [d for d in graph.dynamic_imports if d.resolved is None]
    assert [d.resolved for d in resolved] == ["pkg.b"]
    assert len(unresolved) == 1
    assert unresolved[0].importer == "pkg.a"


def test_cycles_detected(tmp_path):
    make_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "from pkg.b import x\n",
            "pkg/b.py": "from pkg.a import y\n",
        },
    )
    graph = build_graph(tmp_path, [tmp_path / "pkg"], ["pkg"])
    cycles = find_cycles(graph)
    assert len(cycles) == 1
    assert set(cycles[0]) == {"pkg.a", "pkg.b"}


def test_external_import_collection(tmp_path):
    make_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/a.py": "import json\nimport wrapt\n",
        },
    )
    graph = build_graph(tmp_path, [tmp_path / "pkg"], ["pkg"])
    assert graph.external["pkg.a"] == {"json", "wrapt"}


def test_vendor_directories_excluded(tmp_path):
    make_tree(
        tmp_path,
        {
            "pkg/__init__.py": "",
            "pkg/vendor/__init__.py": "from pkg import missing_in_vendor_cycle_a\n",
            "pkg/vendor/missing_in_vendor_cycle_a.py": "from pkg.vendor import missing_in_vendor_cycle_b\n",
            "pkg/vendor/missing_in_vendor_cycle_b.py": "from pkg.vendor import missing_in_vendor_cycle_a\n",
        },
    )
    graph = build_graph(tmp_path, [tmp_path / "pkg"], ["pkg"])
    assert all("vendor" not in module for module in graph.modules)
    assert find_cycles(graph) == []
