"""boundaries gate: negative fixtures must fail, clean trees must pass.

Covers the plan's negative fixtures: a hidden native dependency, a dynamic
forbidden import, an import cycle, and a forbidden namespace reached through
a parent package initializer.
"""

from __future__ import annotations

from pathlib import Path

from package_split.evidence import FAIL
from package_split.evidence import PASS
from package_split.gates import boundaries

from ._support import Args
from ._support import make_report
from ._support import make_tree
from ._support import manifest_text
from ._support import write_pyproject


def run_boundaries(manifest_path: Path, root: Path, **kwargs):
    from package_split.manifest import load_manifest

    manifest = load_manifest(manifest_path)
    report = make_report("boundaries", manifest_path)
    boundaries.run_gate(manifest, Args(**kwargs), report)
    return report


def test_clean_package_passes(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": "__version__ = '1.0'\n",
            "mod.py": "from sample import other\n",
            "other.py": "x = 1\n",
        },
    )
    write_pyproject(tmp_path, "sample", dependencies=[])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    assert report.status == PASS, report.summary()


def test_workspace_src_layout_gates_pass_vacuously(tmp_path):
    # KNOWN FALSE-GREEN, pinned deliberately (codeowners port trial, Nov 2026):
    # module names are derived relative to the manifest root, stripping only
    # a *top-level* "src/" segment. A destination-style workspace layout
    # ("packages/<dist>/src/<module>/...") therefore produces dotted names
    # like "packages.sample.src.sample.mod" which never match the manifest's
    # import_roots. internal_edges filters out every edge, so import-cycles
    # and edge-policy checks pass without evaluating anything -- even with an
    # undeclared external import that a matching layout would fail on.
    # If this test starts failing, name derivation was fixed for workspace
    # layouts; collapse the two halves into one passing assertion.
    workspace_files = {
        "__init__.py": "__version__ = '1.0'\n",
        "mod.py": "import json\n",
    }
    for relative, content in workspace_files.items():
        path = tmp_path / "packages" / "sample" / "src" / "sample" / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    manifest_kwargs = {"import_root": "sample", "source_path": "packages/sample/src", "external": []}
    (tmp_path / "package-contract.toml").write_text(manifest_text(**manifest_kwargs), encoding="utf-8")
    write_pyproject(tmp_path, "sample", dependencies=[])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    assert report.status == PASS, report.summary()

    # Control: the same tree discovered from the package's own directory
    # (per-package manifest, source_path="src") names modules correctly and
    # the undeclared "import json" must fail edge-policy.
    pkg_root = tmp_path / "packages" / "sample"
    make_tree(
        pkg_root,
        "src/sample",
        workspace_files,
        manifest_kwargs={"import_root": "sample", "source_path": "src", "external": []},
    )
    write_pyproject(pkg_root, "sample", dependencies=[])
    report = run_boundaries(pkg_root / "package-contract.toml", pkg_root)
    assert report.status == FAIL


def test_forbidden_static_import_fails(tmp_path):
    # The plan's forbidden-namespace negative fixture: an extracted package
    # must never import the legacy namespace.
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "", "mod.py": "import ddtrace\n"},
        manifest_kwargs={"forbidden": ["ddtrace"]},
    )
    write_pyproject(tmp_path, "sample", dependencies=[])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    assert report.status == FAIL
    edge_policy = [c for c in report.checks if c.name == "edge-policy"]
    assert edge_policy and edge_policy[0].status == FAIL
    reasons = [v["reason"] for v in edge_policy[0].data["violations"]]
    assert any("forbidden namespace" in r for r in reasons), reasons


def test_forbidden_import_via_parent_initializer_fails(tmp_path):
    # Importing sample.deep.mod executes sample/__init__ first; the forbidden
    # import hidden there must still be attributed and rejected.
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": "import ddtrace.internal\n",
            "deep/__init__.py": "",
            "deep/mod.py": "from sample import helper\n",
            "helper.py": "",
        },
        manifest_kwargs={"forbidden": ["ddtrace"]},
    )
    write_pyproject(tmp_path, "sample", dependencies=[])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    assert report.status == FAIL


def test_dynamic_forbidden_import_fails(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": "",
            "mod.py": "import importlib\nimportlib.import_module('ddtrace.ext.ci')\n",
        },
        manifest_kwargs={"forbidden": ["ddtrace"]},
    )
    write_pyproject(tmp_path, "sample", dependencies=[])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    assert report.status == FAIL
    dynamic = [c for c in report.checks if c.name == "dynamic-imports"]
    assert dynamic and dynamic[0].status == FAIL
    reasons = [v["reason"] for v in dynamic[0].data["violations"]]
    assert any("forbidden namespace" in r for r in reasons), reasons


def test_nonliteral_dynamic_import_fails_without_allowlist(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "", "mod.py": "def load(name):\n    return __import__(name)\n"},
    )
    write_pyproject(tmp_path, "sample", dependencies=[])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path, dynamic_import_allowlist=[])
    assert report.status == FAIL
    dynamic = [c for c in report.checks if c.name == "dynamic-imports"]
    assert dynamic and dynamic[0].status == FAIL
    reasons = [v["reason"] for v in dynamic[0].data["violations"]]
    assert any("non-literal dynamic import" in r for r in reasons), reasons


def test_nonliteral_dynamic_import_allowlisted_by_location(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "", "mod.py": "def load(name):\n    return __import__(name)\n"},
    )
    write_pyproject(tmp_path, "sample", dependencies=[])
    report = run_boundaries(
        tmp_path / "package-contract.toml",
        tmp_path,
        dynamic_import_allowlist=["*sample/mod.py*"],
    )
    assert report.status == PASS, report.summary()


def test_import_cycle_fails(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "", "a.py": "from sample import b\n", "b.py": "from sample import a\n"},
    )
    write_pyproject(tmp_path, "sample", dependencies=[])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    assert report.status == FAIL
    assert any(c.name == "import-cycles" and c.status == FAIL for c in report.checks)


def test_undeclared_external_dependency_fails_without_wildcard(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "", "mod.py": "import wrapt\n"},
        manifest_kwargs={"external": []},
    )
    write_pyproject(tmp_path, "sample", dependencies=[])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    assert report.status == FAIL, report.summary()
    edge_policy = [c for c in report.checks if c.name == "edge-policy"]
    assert edge_policy and edge_policy[0].status == FAIL
    reasons = [v["reason"] for v in edge_policy[0].data["violations"]]
    assert any("undeclared external dependency" in r for r in reasons), reasons


def test_hidden_native_dependency_fails(tmp_path):
    # The plan's hidden-native negative fixture: a native-free package whose
    # build metadata pulls in a toolchain (here: cython).
    make_tree(tmp_path, "sample", {"__init__.py": "", "mod.py": ""})
    write_pyproject(tmp_path, "sample", dependencies=[], build_requires=["setuptools", "cython"])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    assert report.status == FAIL
    assert any("native-free" in c.detail and "build" in c.detail for c in report.checks)


def test_undeclared_runtime_dependency_in_pyproject_fails(tmp_path):
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "", "mod.py": ""},
        manifest_kwargs={"runtime": []},
    )
    # pyproject ships a dependency the manifest does not declare.
    write_pyproject(tmp_path, "sample", dependencies=["sneaky-dep"])
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    assert report.status == FAIL
    assert any("not declared" in c.detail for c in report.checks)


def test_versioned_declared_dependency_is_recognized(tmp_path):
    # PEP 508 specifiers must not make a declared dependency look undeclared
    # (regression: bytecode>=0.16.0,<1; python_version~='3.12.0').
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "", "mod.py": ""},
        manifest_kwargs={
            "runtime": ["bytecode", "wrapt", "envier"],
        },
    )
    write_pyproject(
        tmp_path,
        "sample",
        dependencies=[
            "bytecode>=0.16.0,<1; python_version~='3.12.0'",
            "wrapt>=1,!=2.2.0,<3",
            "envier~=0.6.1",
        ],
    )
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    metadata = [c for c in report.checks if c.name == "build-metadata"]
    assert metadata and metadata[0].status == PASS, report.summary()


def test_declared_extras_are_recognized(tmp_path):
    # An optional-dependencies group in pyproject is fine when the manifest
    # declares the same extras group.
    extras_toml = '[dependencies.extras]\nextra = ["opentracing"]\n'
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": ""},
        manifest_kwargs={"extras_section": extras_toml},
    )
    write_pyproject(tmp_path, "sample", dependencies=[])
    (tmp_path / "pyproject.toml").write_text(
        (tmp_path / "pyproject.toml").read_text() + '[project.optional-dependencies]\nextra = ["opentracing>=2,<3"]\n',
        encoding="utf-8",
    )
    report = run_boundaries(tmp_path / "package-contract.toml", tmp_path)
    metadata = [c for c in report.checks if c.name == "build-metadata"]
    assert metadata and metadata[0].status == PASS, report.summary()
