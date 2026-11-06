"""CLI: dispatch, exit codes, evidence files and the wrapper path."""

from __future__ import annotations

from package_split.cli import build_parser
from package_split.cli import main
import pytest

from ._support import REPO_ROOT
from ._support import make_tree
from ._support import write_pyproject


def test_gates_listing(capsys):
    assert main(["gates"]) == 0
    listing = capsys.readouterr().out.split()
    assert set(listing) == {
        "artifacts",
        "boundaries",
        "compatibility",
        "contracts",
        "freeze",
        "lifecycle",
        "migration",
    }


def test_version_flag():
    with pytest.raises(SystemExit) as excinfo:
        build_parser().parse_args(["--version"])
    assert excinfo.value.code == 0


def test_invalid_manifest_exits_two(tmp_path, capsys):
    assert main(["--manifest", str(tmp_path / "missing.toml"), "boundaries"]) == 2


def test_gate_crash_reports_error_and_exits_one(tmp_path, capsys):
    # A crashing gate must surface as a gate error (exit 1) with the
    # evidence file recording it, never a traceback-less silent pass.
    from package_split.gates import REGISTRY

    original = REGISTRY["boundaries"]

    def crashing(manifest, args, report):
        raise RuntimeError("boom")

    REGISTRY["boundaries"] = crashing
    try:
        make_tree(tmp_path, "sample", {"__init__.py": ""})
        write_pyproject(tmp_path, "sample")
        exit_code = main(
            [
                "--manifest",
                str(tmp_path / "package-contract.toml"),
                "--output",
                str(tmp_path / "out"),
                "boundaries",
            ]
        )
    finally:
        REGISTRY["boundaries"] = original
    assert exit_code == 1
    assert "RuntimeError: boom" in capsys.readouterr().out


def test_boundaries_end_to_end_failing_fixture(tmp_path, capsys):
    make_tree(
        tmp_path,
        "sample",
        {"__init__.py": "", "a.py": "from sample import b\n", "b.py": "from sample import a\n"},
    )
    write_pyproject(tmp_path, "sample")
    exit_code = main(
        [
            "--manifest",
            str(tmp_path / "package-contract.toml"),
            "--output",
            str(tmp_path / "out"),
            "boundaries",
        ]
    )
    assert exit_code == 1
    evidence = tmp_path / "out" / "boundaries" / "boundaries.json"
    assert evidence.is_file()
    import json

    data = json.loads(evidence.read_text())
    assert data["gate"] == "boundaries"
    assert data["status"] == "fail"
    assert any(c["name"] == "import-cycles" and c["status"] == "fail" for c in data["checks"])


def test_lifecycle_end_to_end_passing(tmp_path, capsys):
    make_tree(tmp_path, "sample", {"__init__.py": "__version__ = '1.0'\n"})
    write_pyproject(tmp_path, "sample")
    exit_code = main(
        [
            "--manifest",
            str(tmp_path / "package-contract.toml"),
            "--output",
            str(tmp_path / "out"),
            "lifecycle",
        ]
    )
    assert exit_code == 0, capsys.readouterr().out
    assert (tmp_path / "out" / "lifecycle" / "lifecycle.json").is_file()


def test_repository_manifest_is_valid():
    exit_code = main(["--manifest", str(REPO_ROOT / "package-contract.toml"), "gates"])
    assert exit_code == 0
