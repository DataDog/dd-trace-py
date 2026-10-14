"""artifacts gate: install-time checks on crafted wheels plus a real build.

The full build path needs a build tool (uv or python -m build) and network
access for build isolation; those tests skip when the environment cannot
provide them. The install, native-graph and metadata checks run everywhere.
"""

from __future__ import annotations

from pathlib import Path
import shutil
from typing import Any

from package_split import wheelhouse
from package_split.evidence import FAIL
from package_split.evidence import PASS
from package_split.gates import artifacts
import pytest

from ._support import Args
from ._support import craft_wheel
from ._support import make_report
from ._support import make_tree
from ._support import package_files


# Environmental failures in the build path (missing tools, network) are
# skips, not silent passes: the full-build test proves behavior only when
# the environment can actually build.
ENVIRONMENTAL_HINTS = (
    "network",
    "offline",
    "resolve",
    "timed out",
    "connection",
    "command not found",
    "no such file",
    "not found",
)


def _install_checks(tmp_path, wheel: Path, manifest_kwargs=None):
    from package_split.manifest import load_manifest

    manifest_path = make_tree(tmp_path, "sample", {"__init__.py": ""}, manifest_kwargs=manifest_kwargs)
    manifest = load_manifest(manifest_path)
    report = make_report("artifacts", manifest_path)
    artifacts_dict: dict[str, Any] = {"wheel": wheel, "sdist": None}
    artifacts._install_checks(manifest, report, artifacts_dict, tmp_path)
    return report


def test_fresh_install_passes(tmp_path):
    wheel = craft_wheel(
        tmp_path,
        "sample",
        "1.0.0",
        package_files("sample", **{"__init__.py": "__version__ = '1.0.0'\n", "data.txt": "hello"}),
    )
    report = _install_checks(
        tmp_path / "report-tmp",
        wheel,
        manifest_kwargs={"release_extra": 'resources = ["sample:data.txt"]'},
    )
    assert report.status == PASS, report.summary()


def test_missing_package_resource_fails(tmp_path):
    # The plan's negative fixture: a wheel missing a declared resource.
    wheel = craft_wheel(
        tmp_path,
        "sample",
        "1.0.0",
        package_files("sample", **{"__init__.py": ""}),
    )
    report = _install_checks(
        tmp_path / "report-tmp",
        wheel,
        manifest_kwargs={"release_extra": 'resources = ["sample:data.txt"]'},
    )
    assert report.status == FAIL
    assert any(c.name == "resources" and c.status == FAIL for c in report.checks)


def test_hidden_native_dependency_in_resolved_graph_fails(tmp_path):
    # The plan's hidden-native negative fixture: the wheel itself is pure,
    # but a dependency ships a compiled extension.
    # A dependency wheel shipping a compiled extension; the candidate
    # wheel below depends on it.
    craft_wheel(
        tmp_path,
        "nativething",
        "1.0.0",
        {
            "nativething/__init__.py": "",
            "nativething/native_mod.cpython-312-x86_64.so": "\x00\x01",
        },
    )
    wheel = craft_wheel(
        tmp_path,
        "sample",
        "1.0.0",
        package_files("sample", **{"__init__.py": ""}),
        requires=["nativething==1.0.0"],
    )
    report = _install_checks(
        tmp_path / "report-tmp",
        wheel,
        manifest_kwargs={"runtime": ["nativething"]},
    )
    assert report.status == FAIL, report.summary()
    graph_check = [c for c in report.checks if c.name == "resolved-graph-native-free"]
    assert graph_check and graph_check[0].status == FAIL
    assert any("nativething" in f for f in graph_check[0].data["native_files"])


def test_undeclared_wheel_requirement_fails(tmp_path):
    wheel = craft_wheel(
        tmp_path,
        "sample",
        "1.0.0",
        package_files("sample", **{"__init__.py": ""}),
        requires=["sneaky-dep>=1"],
    )
    from package_split.manifest import load_manifest

    manifest_path = make_tree(tmp_path / "m", "sample", {"__init__.py": ""})
    manifest = load_manifest(manifest_path)
    report = make_report("artifacts", manifest_path)
    info = wheelhouse.read_wheel(wheel)
    artifacts._metadata_consistency(manifest, report, info)
    assert report.status == FAIL
    assert any("not declared" in c.detail for c in report.checks)


def _skip_without_build_tooling():
    return shutil.which("uv") is None and shutil.which("build") is None and shutil.which("pip") is None


def _looks_environmental(detail: str) -> bool:
    lowered = detail.lower()
    return any(hint in lowered for hint in ENVIRONMENTAL_HINTS)


@pytest.mark.skipif(_skip_without_build_tooling(), reason="no build tooling available")
def test_full_gate_on_tiny_package(tmp_path):
    # Real end-to-end: a generated pure-python package builds, rebuilds from
    # the sdist and installs cleanly. Skipped when build isolation cannot
    # download a build backend (offline environments).
    from package_split.manifest import load_manifest

    root = tmp_path
    make_tree(
        root,
        "sample",
        {"__init__.py": "__version__ = '1.0'\n", "mod.py": "x = 1\n"},
        manifest_kwargs={"distribution": "sample"},
    )
    (root / "pyproject.toml").write_text(
        '[build-system]\nrequires = ["setuptools"]\nbuild-backend = "setuptools.build_meta"\n\n'
        '[project]\nname = "sample"\nversion = "1.0.0"\ndependencies = []\n',
        encoding="utf-8",
    )
    manifest = load_manifest(root / "package-contract.toml")
    report = make_report("artifacts", root / "package-contract.toml")
    artifacts.run_gate(manifest, Args(), report)
    build_check = next((c for c in report.checks if c.name == "isolated-build"), None)
    if build_check is not None and build_check.status == FAIL and _looks_environmental(build_check.detail):
        pytest.skip("build isolation unavailable: %s" % build_check.detail)
    if report.error and _looks_environmental(report.error):
        pytest.skip("build isolation unavailable: %s" % report.error)
    assert report.status == PASS, report.summary()
    assert any(c.name == "isolated-build" and c.status == PASS for c in report.checks)
    assert any(c.name == "sdist-rebuild" and c.status == PASS for c in report.checks)
