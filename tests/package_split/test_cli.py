"""CLI: dispatch, exit codes and evidence files."""

from __future__ import annotations

import json

from package_split.cli import main

from ._support import make_tree


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

    data = json.loads((tmp_path / "out" / "api-usage" / "api-usage.json").read_text())
    assert data["target"] == "pkg.lib"
    assert data["consumer_files"] == 1
    assert set(data["symbols"]) == {"f"}


def test_api_surface_writes_evidence(tmp_path, monkeypatch, capsys):
    make_tree(tmp_path, "pkg", FILES)
    monkeypatch.chdir(tmp_path)

    assert main(["--output", "out", "api-surface", "--source-path", "pkg"]) == 0
    assert (tmp_path / "out" / "api-surface" / "api-surface.json").is_file()


def test_missing_source_path_exits_two(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)

    assert main(["api-usage", "--module", "pkg.lib", "--source-path", "missing"]) == 2
    assert "source path does not exist" in capsys.readouterr().err
