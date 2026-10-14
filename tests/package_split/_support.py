"""Shared helpers for the package-split harness tests.

Everything here builds self-contained fixture trees (package + manifest) and
wheels (built programmatically as zips with valid dist-info), so gate
behavior can be proven without network access or a compiler toolchain.
"""

from __future__ import annotations

from collections.abc import Iterable
import hashlib
from pathlib import Path
import sys
from typing import Any
from typing import Optional
import zipfile


REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

from package_split.evidence import GateReport  # noqa: E402
from package_split.manifest import load_manifest  # noqa: E402


class Args:
    """Minimal argparse stand-in for calling gate functions directly."""

    def __init__(self, **kwargs: Any) -> None:
        self.__dict__.update(kwargs)

    def __getattr__(self, name: str) -> Any:
        # Missing options default to None, like argparse namespaces without
        # the option ever being set.
        return None


def make_report(gate: str, manifest_path: Path) -> GateReport:
    return GateReport(gate, manifest_path, "test")


def check_by_name(report: GateReport, prefix: str):
    matches = [c for c in report.checks if c.name.startswith(prefix)]
    assert matches, "no check starting with %r in %r" % (prefix, [c.name for c in report.checks])
    return matches[0]


MANIFEST_TEMPLATE = """\
schema_version = 1

[identity]
distribution = "{distribution}"
import_roots = ["{import_root}"]
owner = "test-owner"
source_paths = ["{source_path}"]
destination_repository = "datadog-apm-python-test"

[dependencies]
runtime = [{runtime}]
native_policy = "{native_policy}"
forbidden_namespaces = [{forbidden}]
{extras_section}
[[dependencies.allowed_edges]]
from_prefix = "{import_root}"
to_prefixes = ["{import_root}"]
external = [{external}]

[compatibility]
python = [">=3.9"]
platforms = ["linux"]
dependency_ranges = {{}}
deprecation_policy = "test policy"

[contracts]
supported_exports = {{ "{exports_module}" = [{exports}] }}
behavioral_fixtures = [{fixtures}]
{contracts_extra}
[release]
build_command = []
artifact_expectations = ["wheel", "sdist"]
publishing_owner = "test-owner"
rollback_procedure = "test rollback"
{release_extra}"""


PYPROJECT_TEMPLATE = """\
[build-system]
requires = [{build_requires}]
build-backend = "setuptools.build_meta"

[project]
name = "{name}"
version = "1.0.0"
dependencies = [{dependencies}]
"""


def manifest_text(
    import_root: str = "sample",
    source_path: str = "sample",
    distribution: Optional[str] = None,
    runtime: Optional[Iterable[str]] = None,
    native_policy: str = "native-free",
    forbidden: Optional[Iterable[str]] = None,
    external: Optional[Iterable[str]] = None,
    exports: Optional[Iterable[str]] = None,
    exports_module: Optional[str] = None,
    contracts_extra: str = "",
    fixtures: Optional[Iterable[Iterable[str]]] = None,
    extras_section: str = "",
    release_extra: str = "",
) -> str:
    def toml_list(values: Iterable[str]) -> str:
        return ", ".join('"%s"' % v for v in values)

    import json

    fixtures_toml = ", ".join(json.dumps(list(f)) for f in (fixtures or []))

    return MANIFEST_TEMPLATE.format(
        distribution=distribution or import_root,
        import_root=import_root,
        source_path=source_path,
        runtime=toml_list(runtime if runtime is not None else []),
        native_policy=native_policy,
        forbidden=toml_list(forbidden if forbidden is not None else []),
        # An explicitly empty external list must survive (falsy `or` would
        # silently turn it back into the permissive wildcard).
        external=toml_list(external if external is not None else ["*"]),
        exports=toml_list(exports if exports is not None else ["__version__"]),
        exports_module=exports_module or import_root,
        fixtures=fixtures_toml,
        contracts_extra=contracts_extra,
        extras_section=extras_section,
        release_extra=release_extra,
    )


def write_pyproject(
    root: Path, name: str, dependencies: Optional[Iterable[str]] = None, build_requires: Optional[Iterable[str]] = None
) -> Path:
    def toml_list(values: Iterable[str]) -> str:
        return ", ".join('"%s"' % v for v in values)

    path = root / "pyproject.toml"
    path.write_text(
        PYPROJECT_TEMPLATE.format(
            name=name,
            dependencies=toml_list(dependencies or []),
            build_requires=toml_list(build_requires or ["setuptools"]),
        ),
        encoding="utf-8",
    )
    return path


def make_tree(
    root: Path,
    package_name: str,
    files: dict[str, str],
    manifest_kwargs: Optional[dict[str, Any]] = None,
) -> Path:
    """Create a package tree + manifest; returns the manifest path."""
    for relative, content in files.items():
        path = root / package_name / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
    manifest_path = root / "package-contract.toml"
    kwargs: dict[str, Any] = {"import_root": package_name, "source_path": package_name}
    if manifest_kwargs:
        kwargs.update(manifest_kwargs)
    manifest_path.write_text(manifest_text(**kwargs), encoding="utf-8")
    return manifest_path


def load(root: Path, manifest_path: Path):
    return load_manifest(manifest_path)


# ---------------------------------------------------------------------------
# Wheel crafting: valid wheels without invoking a build backend.
# ---------------------------------------------------------------------------


def _record_entry(data: bytes) -> str:
    digest = hashlib.sha256(data).digest()
    import base64

    return "sha256=" + base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def craft_wheel(
    destination: Path,
    name: str,
    version: str,
    files: dict[str, str],
    requires: Optional[Iterable[str]] = None,
    entry_points: Optional[dict[str, dict[str, str]]] = None,
) -> Path:
    """Write a minimal but valid pure-python wheel by hand.

    ``files`` maps archive paths (e.g. ``pkg/__init__.py``) to contents. A
    valid METADATA, WHEEL and RECORD are generated so pip accepts the wheel
    and the harness can parse it like a real artifact.
    """
    distribution = "%s-%s" % (name.replace("-", "_"), version.replace("-", "_"))
    dist_info = distribution + ".dist-info"
    wheel_name = "%s-%s-py3-none-any.whl" % (name.replace("-", "_"), version)

    metadata_lines = [
        "Metadata-Version: 2.1",
        "Name: %s" % name,
        "Version: %s" % version,
    ]
    for requirement in requires or []:
        metadata_lines.append("Requires-Dist: %s" % requirement)

    archive: dict[str, bytes] = {}
    for path, content in files.items():
        archive[path] = content.encode("utf-8")

    metadata = ("\n".join(metadata_lines) + "\n\n").encode("utf-8")
    wheel_file = b"Wheel-Version: 1.0\nGenerator: package-split-tests\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    archive[dist_info + "/METADATA"] = metadata
    archive[dist_info + "/WHEEL"] = wheel_file
    if entry_points:
        lines = []
        for group, entries in entry_points.items():
            lines.append("[%s]" % group)
            for entry_name, value in entries.items():
                lines.append("%s = %s" % (entry_name, value))
            lines.append("")
        archive[dist_info + "/entry_points.txt"] = "\n".join(lines).encode("utf-8")

    record_lines = []
    for path, data in sorted(archive.items()):
        record_lines.append("%s,%s,%d" % (path, _record_entry(data), len(data)))
    record_lines.append(dist_info + "/RECORD,,")
    record = ("\n".join(record_lines) + "\n").encode("utf-8")
    archive[dist_info + "/RECORD"] = record

    destination.mkdir(parents=True, exist_ok=True)
    wheel_path = destination / wheel_name
    with zipfile.ZipFile(wheel_path, "w") as zf:
        for path, data in sorted(archive.items()):
            zf.writestr(path, data)
    return wheel_path


def package_files(name: str, **modules: str) -> dict[str, str]:
    """Convenience: {"__init__.py": "...", "mod.py": "..."} -> archive paths."""
    return {"%s/%s" % (name, module): content for module, content in modules.items()}
