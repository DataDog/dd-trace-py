"""Regressions for fail-open gates, unsafe archives and evidence serialization."""

from __future__ import annotations

import io
import json
import tarfile
import zipfile

from package_split import wheelhouse
from package_split.astdeps import build_graph
from package_split.cli import build_parser
from package_split.cli import main
from package_split.evidence import FAIL
from package_split.gates import artifacts
from package_split.gates import migration
from package_split.manifest import ManifestError
from package_split.manifest import load_manifest
from package_split.matrices import MatrixError
from package_split.matrices import Scenario
from package_split.matrices import parse_matrix
import pytest

from ._support import craft_wheel
from ._support import make_report
from ._support import make_tree
from .test_contracts import _make_wheels
from .test_contracts import run_contracts
from .test_freeze import FreezeRepo
from .test_lifecycle import ACTIVATION
from .test_lifecycle import run_lifecycle


def test_evidence_serializes_artifact_paths(tmp_path):
    report = make_report("compatibility", tmp_path / "package-contract.toml")
    report.record("candidate", "pass", candidate={"path": tmp_path / "candidate.whl"})
    evidence = json.loads(report.finish(tmp_path).read_text())
    assert evidence["checks"][0]["data"]["candidate"]["path"] == str(tmp_path / "candidate.whl")


@pytest.mark.parametrize("member", ["../outside/escape.py", "/absolute/escape.py"])
def test_archive_paths_cannot_escape_to_sibling_prefix(tmp_path, member):
    wheel = tmp_path / "evil.whl"
    with zipfile.ZipFile(wheel, "w") as archive:
        archive.writestr(member, "bad")
    with pytest.raises(wheelhouse.BuildError):
        wheelhouse.unpack_wheel(wheel, tmp_path / "out")
    sdist = tmp_path / "evil.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo(member)
        info.size = 3
        archive.addfile(info, io.BytesIO(b"bad"))
    with pytest.raises(wheelhouse.BuildError):
        wheelhouse.unpack_sdist(sdist, tmp_path / "out")


@pytest.mark.parametrize("kind", [tarfile.SYMTYPE, tarfile.LNKTYPE, tarfile.FIFOTYPE])
def test_sdist_rejects_links_and_special_files(tmp_path, kind):
    sdist = tmp_path / "evil.tar.gz"
    with tarfile.open(sdist, "w:gz") as archive:
        info = tarfile.TarInfo("pkg/link")
        info.type = kind
        info.linkname = "../../outside"
        archive.addfile(info)
    with pytest.raises(wheelhouse.BuildError):
        wheelhouse.unpack_sdist(sdist, tmp_path / "out")


def test_record_lookup_uses_metadata_and_csv(tmp_path, monkeypatch):
    monkeypatch.setattr(wheelhouse, "_site_packages", lambda _: tmp_path)
    dist_info = tmp_path / "mixed_case-1.0.dist-info"
    dist_info.mkdir()
    (dist_info / "METADATA").write_text("Name: Mixed.Case\nVersion: 1.0\n")
    (dist_info / "RECORD").write_text('"pkg/lib,one.so",,\npkg/../pkg/mod.py,,\n')
    installed = {"Mixed.Case": "1.0"}
    assert wheelhouse.venv_native_files(tmp_path, installed) == ["Mixed.Case:pkg/lib,one.so"]
    assert wheelhouse.distribution_record_files(tmp_path, installed)["Mixed.Case"][1] == "pkg/mod.py"
    (dist_info / "RECORD").unlink()
    with pytest.raises(wheelhouse.BuildError, match="missing RECORD"):
        wheelhouse.venv_native_files(tmp_path, installed, required={"Mixed.Case"})


def test_sdist_parity_compares_bytes_not_just_names(tmp_path, monkeypatch):
    original = craft_wheel(tmp_path / "original", "sample", "1.0", {"sample/__init__.py": "value = 1"})
    rebuilt = craft_wheel(tmp_path / "rebuilt", "sample", "1.0", {"sample/__init__.py": "value = 2"})
    monkeypatch.setattr(wheelhouse, "unpack_sdist", lambda *a: tmp_path)
    monkeypatch.setattr(wheelhouse, "build_distributions", lambda *a: {"wheel": rebuilt})
    manifest_path = make_tree(tmp_path / "source", "sample", {"__init__.py": ""})
    report = make_report("artifacts", manifest_path)
    artifacts._rebuild_check(load_manifest(manifest_path), report, {"wheel": original, "sdist": tmp_path}, tmp_path)
    assert report.status == FAIL
    assert report.checks[0].data["changed_in_rebuild"] == ["sample/__init__.py"]


def test_failed_migration_probe_never_passes(tmp_path, monkeypatch):
    monkeypatch.setattr(migration, "create_venv", lambda *a: None)
    monkeypatch.setattr(migration, "installed_distributions", lambda *a: {})
    monkeypatch.setattr(migration, "_probe", lambda *a, **kw: {"probe_error": "crashed"})
    report = make_report("migration", tmp_path / "package-contract.toml")
    scenario = Scenario("failed-probe", checks={"imports_ok": ["sample"]})
    migration._run_scenario(report, scenario, {}, tmp_path, tmp_path)
    assert report.status == FAIL
    assert "crashed" in report.checks[0].detail


@pytest.mark.parametrize("source", ["", "raise RuntimeError('broken')"])
def test_identically_missing_contracts_never_pass(tmp_path, source):
    baseline, candidate = _make_wheels(tmp_path, source, source)
    assert run_contracts(tmp_path, baseline, candidate).status == FAIL


def test_class_constructor_changes_fail_contracts(tmp_path):
    source = "def helper(): pass\nclass gone_soon:\n    def __init__(self, a=1): pass\n"
    baseline, candidate = _make_wheels(tmp_path, source, source.replace("a=1", "a=2"))
    assert run_contracts(tmp_path, baseline, candidate).status == FAIL


def test_activation_exception_never_passes(tmp_path):
    manifest = make_tree(
        tmp_path,
        "sample",
        {"__init__.py": ("REGISTRY = []\ndef activate(): raise RuntimeError('broken')\ndef deactivate(): pass\n")},
        manifest_kwargs={"contracts_extra": ACTIVATION.format(module="sample")},
    )
    report = run_lifecycle(manifest, activation_singletons=["sample:REGISTRY"])
    assert report.status == FAIL
    assert any("broken" in check.detail for check in report.checks)


def test_declared_but_missing_entry_point_never_passes(tmp_path):
    # A manifest naming a deactivation function the module does not define
    # is a contract error, not a missing optional hook: the probe must fail
    # instead of silently skipping cleanup checks.
    manifest = make_tree(
        tmp_path,
        "sample",
        {
            "__init__.py": (
                "REGISTRY = []\ndef activate(): pass\n"  # no deactivate despite the manifest
            )
        },
        manifest_kwargs={"contracts_extra": ACTIVATION.format(module="sample")},
    )
    report = run_lifecycle(manifest, activation_singletons=["sample:REGISTRY"])
    assert report.status == FAIL
    assert any("deactivate" in check.detail for check in report.checks)


def test_stdout_during_import_does_not_corrupt_probe(tmp_path):
    manifest = make_tree(tmp_path, "sample", {"__init__.py": "print('hello')\n"})
    assert run_lifecycle(manifest).status == "pass"


def test_same_length_import_hook_replacement_fails(tmp_path):
    manifest = make_tree(tmp_path, "sample", {"__init__.py": "import sys\nsys.meta_path[0] = object()\n"})
    assert run_lifecycle(manifest).status == FAIL


def test_freeze_git_failure_never_passes(tmp_path):
    repo = FreezeRepo(tmp_path)
    assert repo.run(head="nonexistent-revision").status == FAIL


def test_freeze_does_not_trust_working_tree_fallback(tmp_path):
    repo = FreezeRepo(tmp_path)
    assert repo.run(base=repo.extraction_revision).status == FAIL


def test_freeze_covers_build_inputs(tmp_path):
    repo = FreezeRepo(tmp_path)
    (tmp_path / "pyproject.toml").write_text("# changed build input\n")
    repo.commit_change("build drift")
    assert repo.run().status == FAIL


@pytest.mark.parametrize(
    "text",
    [
        "schema_version = true\n[[scenario]]\nname = 'x'",
        "schema_version = 1\nscenario = 42",
        "schema_version = 1\n[[scenario]]\nname = 'x'\nrequired = ''",
        "schema_version = 1\n[[scenario]]\nname = 'x'\nexpectations = 42",
        "schema_version = 1\n[[scenario]]\nname = 'x'\ninstalls = [{artifact = 42}]",
        "schema_version = 1\n[[scenario]]\nname = 'x'\n[scenario.expectations]\nimports_okk = ['sample']",
        "schema_version = 1\n[[scenario]]\nname = 'x'\n[scenario.expectations]\ncommands = [[]]",
        "schema_version = 1\n[[scenario]]\nname = 'x'\n[[scenario]]\nname = 'x'",
        "not valid TOML",
    ],
)
def test_malformed_matrices_fail_cleanly(tmp_path, text):
    path = tmp_path / "matrix.toml"
    path.write_text(text)
    with pytest.raises(MatrixError):
        parse_matrix(path, "compatibility")


def test_file_scope_does_not_scan_siblings(tmp_path):
    make_tree(tmp_path, "sample", {"__init__.py": "", "one.py": "import json", "two.py": "import socket"})
    graph = build_graph(tmp_path, [tmp_path / "sample" / "one.py"], ["sample"])
    assert set(graph.modules) == {"sample.one"}


def test_src_layout_module_names(tmp_path):
    make_tree(tmp_path, "src/sample", {"__init__.py": "", "mod.py": "import json"})
    graph = build_graph(tmp_path, [tmp_path / "src/sample"], ["sample"])
    assert set(graph.modules) == {"sample", "sample.mod"}


@pytest.mark.parametrize("source", [None, "def invalid("])
def test_missing_or_unparseable_sources_fail(tmp_path, source):
    if source is not None:
        make_tree(tmp_path, "sample", {"__init__.py": source})
    with pytest.raises((ValueError, SyntaxError)):
        build_graph(tmp_path, [tmp_path / "sample"], ["sample"])


def test_inventory_invalid_manifest_returns_usage_error(tmp_path):
    path = tmp_path / "package-contract.toml"
    path.write_text("schema_version = 99")
    assert main(["api-usage", "--manifest", str(path), "--module", "sample"]) == 2


def test_lifecycle_cli_exposes_registry_observation():
    args = build_parser().parse_args(["lifecycle", "--activation-singletons", "sample:REGISTRY"])
    assert args.activation_singletons == ["sample:REGISTRY"]


@pytest.mark.parametrize("extra", ["build_command = [1]", "build_command = ['']"])
def test_invalid_build_commands_rejected(tmp_path, extra):
    manifest = make_tree(tmp_path, "sample", {"__init__.py": ""}, manifest_kwargs={"release_extra": extra})
    with pytest.raises(ManifestError):
        load_manifest(manifest)
