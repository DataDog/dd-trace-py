"""Tests for .gitlab/validate-ddtrace-package.py, the gate on the publication artifact.

"ddtrace package" and "ddtrace package serverless" run this script over pywheels/, and its
artifact is what reaches PyPI, the dd-trace-py-builds bucket and the private prerelease
index. Until this change a wheel outside the expected set was reported as a warning, and
warnings never reached sys.exit, so cp315 wheels compiled against a 3.15 beta ABI were
published for 31 releases with every job green.

The script only ever runs on the python:3.14.0 image in CI, but tests/internal also runs on
3.9 and 3.10, where the script's own "str | None" annotations and tomllib import are not
available. Hence the module-level skip.

What is being pinned:
  * The full expected matrix passes, so the strict check does not fire on a legitimate set.
  * A cp315 wheel fails the job and the failure names the tag. This is the actual defect.
  * Anything the script prints a "✗" for drives a non-zero exit. That equivalence is the
    property that was missing, and it is asserted on every fixture below.
  * py3-none-any, abi3 and free-threaded cp3XXt wheels are rejected. They already were,
    via the filename-parsing path, which is why widening the unexpected-wheel check could
    not newly fire on them.
  * PYTHON_TAGS agrees with pyproject.toml's requires-python, and disagreeing in either
    direction fails the job rather than silently validating fewer wheels.
  * WIN_ARM64_PYTHON_TAGS is derived, and the derivation matches cibw_skip in
    .github/workflows/build_deploy.yml.
"""

import importlib.util
import itertools
import os
import pathlib
import shutil
import subprocess
import sys
import typing

import pytest


# Skipped at module level rather than with pytestmark: this module imports the validator
# while collecting, and on 3.9 the validator's "str | None" annotations raise at import.
if sys.version_info < (3, 11):
    pytest.skip("validate-ddtrace-package.py needs PEP 604 annotations and tomllib", allow_module_level=True)

_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_VALIDATOR = _REPO_ROOT / ".gitlab" / "validate-ddtrace-package.py"
_PRUNE_SCRIPT = _REPO_ROOT / ".gitlab" / "scripts" / "prune-unsupported-wheels.sh"
_BUILD_DEPLOY_YML = _REPO_ROOT / ".github" / "workflows" / "build_deploy.yml"

VERSION = "9.9.9"

# "build linux" and "build linux serverless" in .gitlab/package.yml are the only jobs whose
# matrix carries cp315-cp315, and they cover manylinux2014 + musllinux on amd64 + arm64.
LINUX_PLATFORMS = [
    "manylinux2014_aarch64.manylinux_2_17_aarch64",
    "manylinux2014_x86_64.manylinux_2_17_x86_64",
    "musllinux_1_2_aarch64",
    "musllinux_1_2_x86_64",
]


def _validator_module():
    spec = importlib.util.spec_from_file_location("validate_ddtrace_package", _VALIDATOR)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


validator = _validator_module()


def _wheel(tag: str, platform: str, flavor: str = "") -> str:
    return f"ddtrace{flavor}-{VERSION}-{tag}-{tag}-{platform}.whl"


def _full_matrix() -> list[str]:
    """Every wheel the pipeline is expected to hand to "ddtrace package"."""
    wheels = [
        _wheel(tag, platform) for tag, platform in itertools.product(validator.PYTHON_TAGS, validator.BASE_PLATFORMS)
    ]
    wheels += [_wheel(tag, "win_arm64") for tag in validator.WIN_ARM64_PYTHON_TAGS]
    return wheels


def _serverless_matrix() -> list[str]:
    return [
        _wheel(tag, platform, flavor="_serverless")
        for tag, platform in itertools.product(validator.PYTHON_TAGS, validator.SERVERLESS_PLATFORMS)
    ]


def _make_dir(tmp_path: pathlib.Path, names, sdist: bool = True) -> pathlib.Path:
    wheels_dir = tmp_path / "pywheels"
    wheels_dir.mkdir(exist_ok=True)
    for name in names:
        (wheels_dir / name).touch()
    if sdist:
        (wheels_dir / f"ddtrace-{VERSION}.tar.gz").touch()
    return wheels_dir


def _run(wheels_dir: pathlib.Path, *extra_args: str, script: typing.Optional[pathlib.Path] = None):
    """Run the validator and assert that printed failures and exit code agree.

    The bug this file guards against was a printed failure that left the exit code at 0,
    so every call site checks the equivalence rather than only the cases that motivated it.
    """
    result = subprocess.run(
        [sys.executable, str(script or _VALIDATOR), str(wheels_dir), *extra_args],
        capture_output=True,
        text=True,
        env={**os.environ, "PACKAGE_VERSION": VERSION},
    )
    output = result.stdout + result.stderr
    assert ("✗" in output) == (result.returncode != 0), (
        f"exit code {result.returncode} disagrees with reported failures:\n{output}"
    )
    return result.returncode, output


def test_full_matrix_passes(tmp_path):
    """The strict check must not fire on the set the pipeline legitimately produces."""
    returncode, output = _run(_make_dir(tmp_path, _full_matrix()))
    assert returncode == 0, output
    assert "SUCCESS" in output


def test_serverless_matrix_passes(tmp_path):
    returncode, output = _run(_make_dir(tmp_path, _serverless_matrix(), sdist=False), "--mode=serverless")
    assert returncode == 0, output


@pytest.mark.parametrize(
    "mode_args,matrix,flavor", [((), _full_matrix, ""), (("--mode=serverless",), _serverless_matrix, "_serverless")]
)
def test_cp315_wheels_fail(tmp_path, mode_args, matrix, flavor):
    """The defect: a cp315 wheel used to be a warning, which never changed the exit code."""
    cp315 = [_wheel("cp315", platform, flavor=flavor) for platform in LINUX_PLATFORMS]
    wheels_dir = _make_dir(tmp_path, matrix() + cp315, sdist=not flavor)

    returncode, output = _run(wheels_dir, *mode_args)

    assert returncode != 0, output
    assert "cp315" in output
    assert f"Unexpected wheels: {len(cp315)} (cp315)" in output
    for name in cp315:
        assert name in output


def test_publish_copy_prunes_cp315_while_s3_copy_keeps_it(tmp_path: pathlib.Path) -> None:
    """The S3 artifact remains complete while the publish copy passes strict validation."""
    cp315: list[str] = [_wheel("cp315", platform) for platform in LINUX_PLATFORMS]
    s3_dir: pathlib.Path = _make_dir(tmp_path, _full_matrix() + cp315)
    pypi_dir: pathlib.Path = tmp_path / "pypi-publish"
    shutil.copytree(s3_dir, pypi_dir)
    adms_source_root: pathlib.Path = tmp_path / "adms-source"
    adms_source_root.mkdir()
    adms_source: pathlib.Path = _make_dir(
        adms_source_root,
        [_wheel(tag, platform) for tag, platform in itertools.product(validator.PYTHON_TAGS, validator.ADMS_PLATFORMS)]
        + [_wheel("cp315", platform) for platform in validator.ADMS_PLATFORMS],
        sdist=False,
    )
    adms_dir: pathlib.Path = tmp_path / "adms-publish"
    shutil.copytree(adms_source, adms_dir)

    prune_result: subprocess.CompletedProcess[str] = subprocess.run(
        ["bash", str(_PRUNE_SCRIPT), str(pypi_dir), str(adms_dir)],
        capture_output=True,
        text=True,
        check=False,
    )

    assert prune_result.returncode == 0, prune_result.stderr
    assert all((s3_dir / name).exists() for name in cp315)
    assert all(not (pypi_dir / name).exists() for name in cp315)
    assert all(not (adms_dir / name).exists() for name in cp315)
    returncode: int
    output: str
    returncode, output = _run(pypi_dir)
    assert returncode == 0, output
    returncode, output = _run(adms_dir, "--mode=adms")
    assert returncode == 0, output


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


def test_unexpected_platform_fails(tmp_path):
    """Unexpected is checked on the whole (tag, platform, flavor) tuple, not just the tag."""
    rogue = _wheel(validator.PYTHON_TAGS[-1], "manylinux_2_28_riscv64")
    returncode, output = _run(_make_dir(tmp_path, _full_matrix() + [rogue]))
    assert returncode != 0, output
    assert rogue in output


def test_missing_wheel_fails(tmp_path):
    """Regression guard: the expected-but-absent direction was already fatal."""
    incomplete = _full_matrix()
    dropped = incomplete.pop()
    returncode, output = _run(_make_dir(tmp_path, incomplete))
    assert returncode != 0, output
    assert dropped in output


@pytest.mark.parametrize(
    "name",
    [
        f"ddtrace-{VERSION}-py3-none-any.whl",
        f"ddtrace-{VERSION}-cp39-abi3-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
        f"ddtrace-{VERSION}-cp314-cp314t-manylinux2014_x86_64.manylinux_2_17_x86_64.whl",
    ],
)
def test_wheels_outside_the_cpython_abi_scheme_fail(tmp_path, name):
    """Pure-Python, abi3 and free-threaded wheels are rejected by the filename parser.

    The pipeline builds none of them today (build_deploy.yml skips cp314t*, and nothing
    produces abi3 or py3-none-any), and they were already fatal before this change. Pinning
    that is what makes the widened unexpected-wheel check safe: none of these can start
    failing as a result of it, because they never reached the expected-set comparison.
    """
    returncode, output = _run(_make_dir(tmp_path, _full_matrix() + [name]))
    assert returncode != 0, output
    assert "Malformed wheel filenames" in output


def test_python_tags_agree_with_requires_python():
    """The shipped PYTHON_TAGS must match what pyproject.toml declares support for."""
    assert validator.check_python_tags_current(_REPO_ROOT) == []


@pytest.mark.parametrize(
    "requires_python,expected_message",
    [
        # Support widened without extending PYTHON_TAGS. This is the cp315 situation, and
        # the shape an unbounded ">=3.9" or ">=3.9,<4.0" also takes.
        (">=3.9,<3.16", "but PYTHON_TAGS stops at"),
        (">=3.9", "but PYTHON_TAGS stops at"),
        # PYTHON_TAGS expecting wheels for an interpreter we no longer publish for.
        (">=3.9,<3.13", "does not support those interpreters"),
    ],
)
def test_stale_python_tags_fail_the_job(tmp_path, requires_python, expected_message):
    """A PYTHON_TAGS list that has drifted from requires-python must be loud, not silent.

    The list capping at cp314 while the build matrix moved to cp315 is why the ABI-broken
    wheels went unnoticed, so drift is an error rather than a smaller validation run.
    """
    fake_root = tmp_path / "repo"
    (fake_root / ".gitlab").mkdir(parents=True)
    shutil.copy(_VALIDATOR, fake_root / ".gitlab" / _VALIDATOR.name)
    (fake_root / "pyproject.toml").write_text(f'[project]\nname = "ddtrace"\nrequires-python = "{requires_python}"\n')

    returncode, output = _run(_make_dir(tmp_path, _full_matrix()), script=fake_root / ".gitlab" / _VALIDATOR.name)

    assert returncode != 0, output
    assert expected_message in output


def test_missing_requires_python_is_an_error(tmp_path):
    """An unreadable cross-check must fail rather than skip. Skipping is the original bug."""
    fake_root = tmp_path / "repo"
    (fake_root / ".gitlab").mkdir(parents=True)
    shutil.copy(_VALIDATOR, fake_root / ".gitlab" / _VALIDATOR.name)
    (fake_root / "pyproject.toml").write_text('[project]\nname = "ddtrace"\n')

    returncode, output = _run(_make_dir(tmp_path, _full_matrix()), script=fake_root / ".gitlab" / _VALIDATOR.name)

    assert returncode != 0, output
    assert "Cannot cross-check PYTHON_TAGS" in output


def test_win_arm64_tags_match_cibw_skip():
    """WIN_ARM64_PYTHON_TAGS is derived; check the derivation against the workflow it mirrors.

    win_arm64 wheels come from .github/workflows/build_deploy.yml via "download win_arm64
    wheels", so cibw_skip is the authority on which tags exist for that platform.
    """
    yaml = pytest.importorskip("yaml")

    class _Loader(yaml.SafeLoader):
        pass

    _Loader.add_constructor("!reference", lambda loader, node: None)
    workflow = yaml.load(_BUILD_DEPLOY_YML.read_text(), Loader=_Loader)

    cibw_skip = next(
        job["with"]["cibw_skip"]
        for job in workflow["jobs"].values()
        if isinstance(job, dict) and "cibw_skip" in job.get("with", {})
    )

    for tag in validator.PYTHON_TAGS:
        skipped = f"{tag}-win_arm64" in cibw_skip.split()
        assert skipped != (tag in validator.WIN_ARM64_PYTHON_TAGS), (
            f"{tag}: cibw_skip and WIN_ARM64_PYTHON_TAGS disagree about win_arm64"
        )
