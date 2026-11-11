"""Tests for scripts/check-libdatadog-pins.sh."""

from __future__ import annotations

import pathlib
import subprocess

import pytest


_SCRIPT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "check-libdatadog-pins.sh"

_URL: str = "https://github.com/DataDog/libdatadog"


def _run(tmp_path: pathlib.Path, files: dict[str, str]) -> subprocess.CompletedProcess[str]:
    """Run the checker inside a throwaway git repository containing ``files``."""
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    for name, content in files.items():
        path: pathlib.Path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    subprocess.run(["git", "add", "-A"], cwd=tmp_path, check=True)
    return subprocess.run(["bash", str(_SCRIPT)], cwd=tmp_path, capture_output=True, text=True)


def _dep(name: str, pin: str, extra: str = "") -> str:
    return f'{name} = {{ git = "{_URL}", {pin}{extra} }}\n'


def test_release_tags_that_agree_pass(tmp_path: pathlib.Path) -> None:
    manifest: str = _dep("libdd-a", 'rev = "v48.0.0"') + _dep("libdd-b", 'rev = "v48.0.0"', ", optional = true")
    result = _run(tmp_path, {"src/native/Cargo.toml": manifest})
    assert result.returncode == 0, result.stdout + result.stderr
    assert "v48.0.0" in result.stdout


@pytest.mark.parametrize(
    "pin",
    [
        'rev = "a4cdfb9c081e40069cc6cc8fd5db243e303a794e"',
        'rev = "a4cdfb9"',
        'branch = "main"',
        'tag = "v48.0.0"',
    ],
)
def test_non_release_pins_fail(tmp_path: pathlib.Path, pin: str) -> None:
    result = _run(tmp_path, {"src/native/Cargo.toml": _dep("libdd-a", pin)})
    assert result.returncode == 1
    assert "must pin a release tag" in result.stdout


def test_mixed_release_tags_fail(tmp_path: pathlib.Path) -> None:
    manifest: str = _dep("libdd-a", 'rev = "v48.0.0"') + _dep("libdd-b", 'rev = "v47.0.0"')
    result = _run(tmp_path, {"src/native/Cargo.toml": manifest})
    assert result.returncode == 1
    assert "same release" in result.stdout


def test_mixed_release_tags_across_manifests_fail(tmp_path: pathlib.Path) -> None:
    result = _run(
        tmp_path,
        {
            "src/native/Cargo.toml": _dep("libdd-a", 'rev = "v48.0.0"'),
            "src/other/Cargo.toml": _dep("libdd-b", 'rev = "v46.1.2"'),
        },
    )
    assert result.returncode == 1
    assert "v46.1.2" in result.stdout and "v48.0.0" in result.stdout


def test_allow_marker_exempts_a_single_dependency(tmp_path: pathlib.Path) -> None:
    manifest: str = (
        _dep("libdd-a", 'rev = "v48.0.0"')
        + _dep("libdd-b", 'rev = "deadbeef"').rstrip("\n")
        + ("  # libdatadog-pin: allow (waiting for the next release)\n")
    )
    result = _run(tmp_path, {"src/native/Cargo.toml": manifest})
    assert result.returncode == 0, result.stdout + result.stderr


def test_comments_and_unrelated_git_dependencies_are_ignored(tmp_path: pathlib.Path) -> None:
    manifest: str = (
        _dep("libdd-a", 'rev = "v48.0.0"')
        + f'# old = {{ git = "{_URL}", rev = "deadbeef" }}\n'
        + 'other = { git = "https://github.com/someone/else", rev = "deadbeef" }\n'
        + 'serde = "1"\n'
    )
    result = _run(tmp_path, {"src/native/Cargo.toml": manifest})
    assert result.returncode == 0, result.stdout + result.stderr


def test_dot_git_suffix_is_still_checked(tmp_path: pathlib.Path) -> None:
    manifest: str = f'libdd-a = {{ git = "{_URL}.git", rev = "deadbeef" }}\n'
    result = _run(tmp_path, {"src/native/Cargo.toml": manifest})
    assert result.returncode == 1


def test_no_libdatadog_dependencies_is_a_no_op(tmp_path: pathlib.Path) -> None:
    result = _run(tmp_path, {"crate/Cargo.toml": 'serde = "1"\n'})
    assert result.returncode == 0
    assert result.stdout == ""
