"""Tests for .gitlab/validate-ddtrace-package.py, the gate on the publication artifact.

release_pypi and the adms publish path run this script over the pruned pywheels-publish
copy. Until this change a wheel outside the expected set was reported as a warning, and
warnings never reached sys.exit, so cp315 wheels compiled against a 3.15 beta ABI reached
PyPI with every job green.

The script only ever runs on the python:3.14.0 image in CI, but tests/internal also runs on
3.9 and 3.10, where the script's own "str | None" annotations and tomllib import are not
available. Hence the module-level skip.

What is being pinned:
  * The full expected matrix passes, so the strict check does not fire on a legitimate set.
  * A cp315 wheel fails the job and the failure names the tag. This is the actual defect.
  * Anything the script prints a "✗" for drives a non-zero exit. That equivalence is the
    property that was missing, and it is asserted on every fixture below.
  * PYTHON_TAGS agrees with pyproject.toml's requires-python, and disagreeing in either
    direction fails the job rather than silently validating fewer wheels.
  * --mode=adms accepts the manylinux-only publication matrix used by the adms upload path.
  * --mode=adms-macos accepts the macOS-only matrix used by the macOS adms patch job.
  * Phase 4 summary lines are mode-specific (no unconditional base/win_arm64/serverless counts).
"""

import importlib.machinery
import importlib.util
import itertools
import os
import pathlib
import shutil
import subprocess
import sys
import types
import typing

import pytest


# Skipped at module level rather than with pytestmark: this module imports the validator
# while collecting, and on 3.9 the validator's "str | None" annotations raise at import.
if sys.version_info < (3, 11):
    pytest.skip("validate-ddtrace-package.py needs PEP 604 annotations and tomllib", allow_module_level=True)

_REPO_ROOT: pathlib.Path = pathlib.Path(__file__).resolve().parents[2]
_VALIDATOR: pathlib.Path = _REPO_ROOT / ".gitlab" / "validate-ddtrace-package.py"

VERSION: str = "9.9.9"

# "build linux" and "build linux serverless" in .gitlab/package.yml are the only jobs whose
# matrix carries cp315-cp315, and they cover manylinux2014 + musllinux on amd64 + arm64.
LINUX_PLATFORMS: list[str] = [
    "manylinux2014_aarch64.manylinux_2_17_aarch64",
    "manylinux2014_x86_64.manylinux_2_17_x86_64",
    "musllinux_1_2_aarch64",
    "musllinux_1_2_x86_64",
]


def _validator_module() -> types.ModuleType:
    spec: importlib.machinery.ModuleSpec | None = importlib.util.spec_from_file_location(
        "validate_ddtrace_package", _VALIDATOR
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load validator from {_VALIDATOR}")
    module: types.ModuleType = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator: types.ModuleType = _validator_module()


def _wheel(tag: str, platform: str, flavor: str = "") -> str:
    return f"ddtrace{flavor}-{VERSION}-{tag}-{tag}-{platform}.whl"


def _full_matrix() -> list[str]:
    """Every wheel the main publication path is expected to validate."""
    wheels: list[str] = [
        _wheel(tag, platform) for tag, platform in itertools.product(validator.PYTHON_TAGS, validator.BASE_PLATFORMS)
    ]
    wheels += [_wheel(tag, "win_arm64") for tag in validator.WIN_ARM64_PYTHON_TAGS]
    return wheels


def _make_dir(tmp_path: pathlib.Path, names: list[str], sdist: bool = True) -> pathlib.Path:
    wheels_dir: pathlib.Path = tmp_path / "pywheels"
    wheels_dir.mkdir(exist_ok=True)
    for name in names:
        (wheels_dir / name).touch()
    if sdist:
        (wheels_dir / f"ddtrace-{VERSION}.tar.gz").touch()
    return wheels_dir


def _run(wheels_dir: pathlib.Path, *extra_args: str, script: typing.Optional[pathlib.Path] = None) -> tuple[int, str]:
    """Run the validator and assert that printed failures and exit code agree.

    The bug this file guards against was a printed failure that left the exit code at 0,
    so every call site checks the equivalence rather than only the cases that motivated it.
    """
    result: subprocess.CompletedProcess[str] = subprocess.run(
        [sys.executable, str(script or _VALIDATOR), str(wheels_dir), *extra_args],
        capture_output=True,
        text=True,
        env={**os.environ, "PACKAGE_VERSION": VERSION},
    )
    output: str = result.stdout + result.stderr
    assert ("✗" in output) == (result.returncode != 0), (
        f"exit code {result.returncode} disagrees with reported failures:\n{output}"
    )
    return result.returncode, output


def test_full_matrix_passes(tmp_path: pathlib.Path) -> None:
    """The strict check must not fire on the set the pipeline legitimately produces."""
    returncode: int
    output: str
    returncode, output = _run(_make_dir(tmp_path, _full_matrix()))
    assert returncode == 0, output
    assert "SUCCESS" in output


def test_cp315_wheels_fail(tmp_path: pathlib.Path) -> None:
    """The defect: a cp315 wheel used to be a warning, which never changed the exit code."""
    cp315: list[str] = [_wheel("cp315", platform) for platform in LINUX_PLATFORMS]
    wheels_dir: pathlib.Path = _make_dir(tmp_path, _full_matrix() + cp315)

    returncode: int
    output: str
    returncode, output = _run(wheels_dir)

    assert returncode != 0, output
    assert "cp315" in output
    assert f"Unexpected wheels: {len(cp315)} (cp315)" in output
    for name in cp315:
        assert name in output


def test_adms_publish_copy_validates_pruned_manylinux_set(tmp_path: pathlib.Path) -> None:
    """The adms validator mode accepts its manylinux-only publication matrix."""
    wheels: list[str] = [
        _wheel(tag, platform) for tag, platform in itertools.product(validator.PYTHON_TAGS, validator.ADMS_PLATFORMS)
    ]
    wheels_dir: pathlib.Path = _make_dir(tmp_path, wheels, sdist=False)

    returncode: int
    output: str
    returncode, output = _run(wheels_dir, "--mode=adms")
    assert returncode == 0, output
    assert "manylinux2014 platforms" in output
    assert "base platforms" not in output
    assert "win_arm64" not in output


def test_adms_macos_publish_copy_validates_macos_set(tmp_path: pathlib.Path) -> None:
    """The macOS adms patch job validates only macosx wheels, not the manylinux matrix."""
    wheels: list[str] = [
        _wheel(tag, platform)
        for tag, platform in itertools.product(validator.PYTHON_TAGS, validator.ADMS_MACOS_PLATFORMS)
    ]
    wheels_dir: pathlib.Path = _make_dir(tmp_path, wheels, sdist=False)

    returncode: int
    output: str
    returncode, output = _run(wheels_dir, "--mode=adms-macos")
    assert returncode == 0, output
    assert "macOS platforms" in output
    assert "manylinux2014 platforms" not in output


def test_unexpected_platform_fails(tmp_path: pathlib.Path) -> None:
    """Unexpected is checked on the whole (tag, platform, flavor) tuple, not just the tag."""
    rogue: str = _wheel(validator.PYTHON_TAGS[-1], "manylinux_2_28_riscv64")
    returncode: int
    output: str
    returncode, output = _run(_make_dir(tmp_path, _full_matrix() + [rogue]))
    assert returncode != 0, output
    assert rogue in output


def test_python_tags_agree_with_requires_python() -> None:
    """The shipped PYTHON_TAGS must match what pyproject.toml declares support for."""
    assert validator.check_python_tags_current(_REPO_ROOT) == []


@pytest.mark.parametrize(
    "requires_python,expected_message",
    [
        # Support widened without extending PYTHON_TAGS (the cp315 situation).
        (">=3.9,<3.16", "but PYTHON_TAGS stops at"),
        # PYTHON_TAGS expecting wheels for an interpreter we no longer publish for.
        (">=3.9,<3.13", "does not support those interpreters"),
    ],
)
def test_stale_python_tags_fail_the_job(tmp_path: pathlib.Path, requires_python: str, expected_message: str) -> None:
    """A PYTHON_TAGS list that has drifted from requires-python must be loud, not silent.

    The list capping at cp314 while the build matrix moved to cp315 is why the ABI-broken
    wheels went unnoticed, so drift is an error rather than a smaller validation run.
    """
    fake_root: pathlib.Path = tmp_path / "repo"
    (fake_root / ".gitlab").mkdir(parents=True)
    shutil.copy(_VALIDATOR, fake_root / ".gitlab" / _VALIDATOR.name)
    (fake_root / "pyproject.toml").write_text(f'[project]\nname = "ddtrace"\nrequires-python = "{requires_python}"\n')

    returncode: int
    output: str
    returncode, output = _run(_make_dir(tmp_path, _full_matrix()), script=fake_root / ".gitlab" / _VALIDATOR.name)

    assert returncode != 0, output
    assert expected_message in output
