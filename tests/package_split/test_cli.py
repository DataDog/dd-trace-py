"""CLI: dispatch, exit codes and evidence files."""

from __future__ import annotations

import json

from package_split.cli import main
import pytest

from ._support import make_tree
from .test_freeze import FreezeRepo


FILES = {
    "__init__.py": "",
    "lib.py": "def f():\n    pass\n",
    "consumer.py": "from pkg.lib import f\nf()\n",
}


def test_no_command_exits_two(capsys):
    assert main([]) == 2


def test_api_usage_writes_evidence(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, "pkg", FILES)
    monkeypatch.chdir(tmp_path)

    assert main(["--output", "out", "api-usage", "--module", "pkg.lib", "--source-path", "pkg"]) == 0

    data = json.loads(next((tmp_path / "out" / "api-usage").glob("*/api-usage.json")).read_text())
    assert data["target"] == "pkg.lib"
    assert data["source_paths"] == ["pkg"]
    assert data["consumer_files"] == 1
    assert set(data["symbols"]) == {"f"}


def test_evidence_is_scoped_and_never_clobbers(tmp_path, monkeypatch, capsys):
    # The workflow inventories the same target over several scopes
    # (production, then tests); each scope writes its own evidence file so
    # earlier runs survive instead of being silently overwritten.
    make_tree(tmp_path, "pkg", FILES)
    monkeypatch.chdir(tmp_path)

    assert main(["--output", "out", "api-usage", "--module", "pkg.lib", "--source-path", "pkg"]) == 0
    assert main(["--output", "out", "api-usage", "--module", "pkg.lib", "--source-path", "."]) == 0

    evidence = list((tmp_path / "out" / "api-usage").glob("*/api-usage.json"))
    assert len(evidence) == 2
    assert {tuple(json.loads(path.read_text())["source_paths"]) for path in evidence} == {("pkg",), (".",)}


def test_different_targets_keep_separate_evidence(tmp_path, monkeypatch):
    make_tree(tmp_path, "pkg", FILES | {"other.py": "from pkg.lib import f\nf()\n"})
    monkeypatch.chdir(tmp_path)
    for target in ("pkg.lib", "pkg.other"):
        assert main(["--output", "out", "api-usage", "--module", target, "--source-path", "pkg"]) == 0
    evidence = list((tmp_path / "out/api-usage").glob("*/api-usage.json"))
    assert len(evidence) == 2
    assert {json.loads(path.read_text())["target"] for path in evidence} == {"pkg.lib", "pkg.other"}


def test_similarly_named_scopes_keep_separate_evidence(tmp_path, monkeypatch):
    make_tree(tmp_path, "a/b", FILES)
    make_tree(tmp_path, "a_b", FILES)
    monkeypatch.chdir(tmp_path)
    for scope in ("a/b", "a_b"):
        assert main(["--output", "out", "api-surface", "--source-path", scope]) == 0
    assert len(list((tmp_path / "out/api-surface").glob("*/api-surface.json"))) == 2


def test_only_source_freeze_is_a_gate(capsys):
    assert main(["gates"]) == 0
    assert capsys.readouterr().out.splitlines() == ["freeze"]


@pytest.mark.parametrize("drift", [False, True])
def test_freeze_cli_without_a_package_contract(tmp_path, monkeypatch, drift):
    repo = FreezeRepo(tmp_path)
    if drift:
        (tmp_path / "sample/mod.py").write_text("value = 3\n", encoding="utf-8")
        repo.commit_change("source drift")
    monkeypatch.chdir(tmp_path)
    assert not (tmp_path / "package-contract.toml").exists()
    assert main(["freeze", "--base", "HEAD", "--output", "out"]) == int(drift)
    data = json.loads((tmp_path / "out/freeze/freeze.json").read_text())
    assert data["status"] == ("fail" if drift else "pass")


def test_freeze_cli_records_a_crash(tmp_path, monkeypatch):
    from package_split.gates import freeze

    def crashing(*args):
        raise RuntimeError("boom")

    monkeypatch.setattr(freeze, "run_gate", crashing)
    monkeypatch.chdir(tmp_path)
    assert main(["freeze", "--base", "HEAD", "--output", "out"]) == 1
    data = json.loads((tmp_path / "out/freeze/freeze.json").read_text())
    assert data["error"] == "RuntimeError: boom"


def test_api_surface_writes_evidence(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, "pkg", FILES)
    monkeypatch.chdir(tmp_path)

    assert main(["--output", "out", "api-surface", "--source-path", "pkg"]) == 0
    assert next((tmp_path / "out" / "api-surface").glob("*/api-surface.json")).is_file()


def test_missing_source_path_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["api-usage", "--module", "pkg.lib", "--source-path", "missing"]) == 2
    assert "source path does not exist" in capsys.readouterr().err


def test_missing_explicit_zones_exits_two(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, "pkg", FILES)
    monkeypatch.chdir(tmp_path)

    assert main(["api-surface", "--source-path", "pkg", "--zones", "missing.json"]) == 2


def test_import_roots_follow_src_layout(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path / "src", "pkg", FILES)
    monkeypatch.chdir(tmp_path)

    assert main(["--output", "out", "api-surface", "--source-path", "src/pkg"]) == 0
    data = json.loads(next((tmp_path / "out" / "api-surface").glob("*/api-surface.json")).read_text())
    assert data["report"]["roots"] == ["pkg"]


def test_current_directory_source_path(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, "pkg", FILES)
    monkeypatch.chdir(tmp_path)

    assert main(["--output", "out", "api-usage", "--module", "pkg.lib", "--source-path", "."]) == 0


def test_allow_absent_target_scans_consumer_only_scope(tmp_path, monkeypatch, capsys):
    # tests/ contains consumers of a module that lives under ddtrace/ (or
    # here: pkg-consumers of a target defined outside the scanned scope).
    make_tree(tmp_path, "pkg", FILES)
    consumer_only = tmp_path / "tests" / "test_it.py"
    consumer_only.parent.mkdir(parents=True)
    consumer_only.write_text("from pkg.lib import f\nf()\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    # Without the flag this fails closed (typo protection).
    assert main(["--output", "out", "api-usage", "--module", "pkg.lib", "--source-path", "tests"]) == 2
    assert "--allow-absent-target" in capsys.readouterr().err

    assert (
        main(["--output", "out", "api-usage", "--module", "pkg.lib", "--source-path", "tests", "--allow-absent-target"])
        == 0
    )
    data = json.loads(next((tmp_path / "out" / "api-usage").glob("*/api-usage.json")).read_text())
    assert data["consumer_files"] == 1
    assert set(data["symbols"]) == {"f"}


def test_zones_file_without_mapping_exits_two(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, "pkg", FILES)
    (tmp_path / "layers.json").write_text('{"zone": {}}', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["api-surface", "--source-path", "pkg", "--zones", "layers.json"]) == 2
    assert "no 'zones' mapping" in capsys.readouterr().err


def test_unparsable_files_are_skipped_and_reported(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, "pkg", FILES | {"broken.py": "def (:\n"})
    monkeypatch.chdir(tmp_path)

    assert main(["--output", "out", "api-usage", "--module", "pkg.lib", "--source-path", "pkg"]) == 0
    assert "pkg/broken.py" in capsys.readouterr().err
    data = json.loads(next((tmp_path / "out" / "api-usage").glob("*/api-usage.json")).read_text())
    assert data["consumer_files"] == 1
    assert data["unparsed"][0].startswith("pkg/broken.py")
