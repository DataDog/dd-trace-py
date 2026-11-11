"""Manifest schema tests: validation is strict and versioned."""

from __future__ import annotations

from package_split.manifest import ManifestError
from package_split.manifest import load_manifest
from package_split.manifest import validate_manifest
import pytest

from ._support import REPO_ROOT
from ._support import manifest_text


def test_repository_manifest_loads():
    manifest = load_manifest(REPO_ROOT / "package-contract.toml")
    assert manifest.distribution == "ddtrace"
    assert manifest.import_roots == ["ddtrace"]
    assert manifest.native_policy == "native-required"
    assert manifest.extras["opentracing"] == ["opentracing"]


def test_valid_generated_manifest(tmp_path):
    path = tmp_path / "package-contract.toml"
    path.write_text(manifest_text(), encoding="utf-8")
    manifest = load_manifest(path)
    assert manifest.source_paths == [tmp_path / "sample"]
    assert manifest.supported_exports == {"sample": ["__version__"]}


@pytest.mark.parametrize(
    "text,issue",
    [
        ("schema_version = 2\n", "not supported by this harness"),
        ("[identity]\n", "missing required field 'schema_version'"),
        (
            manifest_text() + "\n[whoops]\nkey = 1\n",
            "unknown top-level table",
        ),
        (
            manifest_text().replace('native_policy = "native-free"', 'native_policy = "native-sometimes"'),
            "is not one of",
        ),
        (
            manifest_text().replace('import_roots = ["sample"]', "import_roots = []"),
            "at least one import root",
        ),
        (
            manifest_text().replace('source_paths = ["sample"]', "source_paths = []"),
            "must not be empty",
        ),
        (
            manifest_text().replace('deprecation_policy = "test policy"', 'deprecation_policy = ""'),
            "must not be empty",
        ),
        (
            manifest_text().replace('supported_exports = { "sample" = ["__version__"] }', "supported_exports = {}"),
            "at least one module is required",
        ),
    ],
)
def test_invalid_manifests(tmp_path, text, issue):
    path = tmp_path / "package-contract.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ManifestError) as excinfo:
        load_manifest(path)
    assert any(issue in problem for problem in excinfo.value.issues), excinfo.value.issues


def test_allowed_edges_validation(tmp_path):
    # Insert a malformed rule before the valid one.
    text = manifest_text().replace(
        "[[dependencies.allowed_edges]]",
        '[[dependencies.allowed_edges]]\nfrom_prefix = "1notvalid"\nto_prefixes = []\n\n[[dependencies.allowed_edges]]',
        1,
    )
    path = tmp_path / "package-contract.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ManifestError) as excinfo:
        load_manifest(path)
    issues = excinfo.value.issues
    assert any("invalid module prefix" in i for i in issues), issues
    assert any("to_prefixes" in i for i in issues), issues


def test_activation_api_validation(tmp_path):
    text = manifest_text(
        contracts_extra='[contracts.activation_api]\nmodule = "sample"\n',
    )
    path = tmp_path / "package-contract.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ManifestError) as excinfo:
        load_manifest(path)
    assert any("activation_api" in i for i in excinfo.value.issues)


def test_unknown_fields_rejected(tmp_path):
    text = manifest_text(release_extra="mystery_field = 1\n")
    path = tmp_path / "package-contract.toml"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(ManifestError) as excinfo:
        load_manifest(path)
    assert any("unknown field" in i for i in excinfo.value.issues)


def test_validate_manifest_returns_issues_for_non_dict():
    issues = validate_manifest(["not", "a", "table"])
    assert issues == ["manifest: expected a TOML document at the top level"]
