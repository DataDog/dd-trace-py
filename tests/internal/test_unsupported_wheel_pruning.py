"""Prune unsupported tags from PyPI/adms; S3 SHA index keeps them for gate installs."""

from __future__ import annotations

from collections.abc import Iterator
import os
import pathlib
import shutil
import subprocess
from typing import Any

import pytest
import yaml


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_GITLAB_DIR = _REPO_ROOT / ".gitlab"
_PRUNE_SCRIPT = _GITLAB_DIR / "scripts" / "prune-unsupported-wheels.sh"
_UPLOAD_TO_S3 = _GITLAB_DIR / "scripts" / "upload-wheels-to-s3.sh"
_PACKAGE_YML = _GITLAB_DIR / "package.yml"
_RELEASE_YML = _GITLAB_DIR / "release.yml"

VERSION = "9.9.9"

# Transcribed from .gitlab/validate-ddtrace-package.py: 6 Python tags x 8 base platforms,
# plus win_arm64 for 3.11+, plus the sdist.
SUPPORTED_TAGS = ["cp39", "cp310", "cp311", "cp312", "cp313", "cp314"]
WIN_ARM64_TAGS = ["cp311", "cp312", "cp313", "cp314"]
BASE_PLATFORMS = [
    "macosx_14_0_arm64",
    "macosx_14_0_x86_64",
    "manylinux2014_aarch64.manylinux_2_17_aarch64",
    "manylinux2014_x86_64.manylinux_2_17_x86_64",
    "musllinux_1_2_aarch64",
    "musllinux_1_2_x86_64",
    "win32",
    "win_amd64",
]
# "build linux" and "build linux serverless" in .gitlab/package.yml are the only jobs whose
# matrix carries cp315-cp315, and they cover manylinux2014 + musllinux on amd64 + arm64.
# macOS and Windows stop at 3.14.
CP315_PLATFORMS = [
    "manylinux2014_aarch64.manylinux_2_17_aarch64",
    "manylinux2014_x86_64.manylinux_2_17_x86_64",
    "musllinux_1_2_aarch64",
    "musllinux_1_2_x86_64",
]

SUPPORTED_WHEELS = [
    f"ddtrace-{VERSION}-{tag}-{tag}-{platform}.whl" for tag in SUPPORTED_TAGS for platform in BASE_PLATFORMS
] + [f"ddtrace-{VERSION}-{tag}-{tag}-win_arm64.whl" for tag in WIN_ARM64_TAGS]
CP315_WHEELS = [f"ddtrace-{VERSION}-cp315-cp315-{platform}.whl" for platform in CP315_PLATFORMS] + [
    f"ddtrace_serverless-{VERSION}-cp315-cp315-{platform}.whl" for platform in CP315_PLATFORMS
]
SDIST = f"ddtrace-{VERSION}.tar.gz"

PYPI_ADMS_COMMANDS: tuple[str, ...] = ("twine upload", "adms first-party python upload")
KNOWN_PYPI_ADMS_PUBLISHERS: set[str] = {
    ".gitlab/package.yml",
    ".gitlab/release.yml",
}


class _GitLabLoader(yaml.SafeLoader):
    """SafeLoader that tolerates GitLab's !reference tag."""


_GitLabLoader.add_constructor("!reference", lambda loader, node: None)


def _resolve(doc: dict[str, Any], job: str | None, key: str) -> Any:
    """Walk the extends: chain until key is found, the way GitLab does."""
    while job is not None:
        if key in doc[job]:
            return doc[job][key]
        job = doc[job].get("extends")
    raise AssertionError(f"{key!r} not found in the extends chain of the job")


def _script_body(path: pathlib.Path, job: str) -> str:
    """A job's script: block, joined as GitLab joins it."""
    doc: dict[str, Any] = yaml.load(path.read_text(), Loader=_GitLabLoader)
    script: Any = _resolve(doc, job, "script")
    assert isinstance(script, list), f"{job} script: is expected to be a list"
    return "\n".join(entry for entry in script if isinstance(entry, str))


def _gitlab_text_files() -> Iterator[tuple[pathlib.Path, str]]:
    for path in sorted(_GITLAB_DIR.rglob("*")):
        if not path.is_file():
            continue
        try:
            yield path, path.read_text()
        except UnicodeDecodeError:
            continue


def _assert_prunes_before_publishing(body: str, publish_command: str) -> None:
    prune_at: int = body.find("prune-unsupported-wheels.sh")
    publish_at: int = body.find(publish_command)
    assert prune_at != -1, f"prune-unsupported-wheels.sh is not called before {publish_command!r}"
    assert publish_at != -1, f"the extracted script no longer runs {publish_command!r}"
    assert prune_at < publish_at, f"the prune call must come before {publish_command!r}"


@pytest.fixture()
def prune(tmp_path):
    """Run the real prune script against fixture directories."""
    shell = shutil.which("bash")
    if shell is None:
        pytest.skip("bash is required to run the prune script")

    def run(*dirs, contents=None):
        contents = contents or {}
        for name in dirs:
            target = tmp_path / name
            target.mkdir(exist_ok=True)
            for filename in contents.get(name, ()):
                (target / filename).touch()
        return subprocess.run(
            [shell, str(_PRUNE_SCRIPT), *dirs],
            cwd=str(tmp_path),
            capture_output=True,
            text=True,
            check=False,
        )

    return run


def _listing(tmp_path, name):
    return sorted(p.name for p in (tmp_path / name).iterdir())


def test_cp315_wheels_are_deleted_and_everything_else_survives(prune, tmp_path):
    proc = prune("pywheels", contents={"pywheels": SUPPORTED_WHEELS + CP315_WHEELS + [SDIST]})

    assert proc.returncode == 0, proc.stderr
    assert _listing(tmp_path, "pywheels") == sorted(SUPPORTED_WHEELS + [SDIST])


def test_both_ddtrace_and_serverless_cp315_wheels_are_deleted(prune, tmp_path):
    proc = prune("pywheels", contents={"pywheels": CP315_WHEELS})

    assert proc.returncode == 0, proc.stderr
    assert _listing(tmp_path, "pywheels") == []
    assert "ddtrace_serverless" in proc.stdout


def test_pruning_is_a_no_op_when_no_cp315_is_present(prune, tmp_path):
    proc = prune("pywheels", contents={"pywheels": SUPPORTED_WHEELS + [SDIST]})

    assert proc.returncode == 0, proc.stderr
    assert _listing(tmp_path, "pywheels") == sorted(SUPPORTED_WHEELS + [SDIST])
    assert "Pruning" not in proc.stdout


def test_debug_symbol_archives_are_left_alone(prune, tmp_path):
    symbols = f"ddtrace-{VERSION}-cp315-cp315-musllinux_1_2_x86_64.zip"
    proc = prune("pywheels", contents={"pywheels": [symbols, SDIST]})

    assert proc.returncode == 0, proc.stderr
    assert _listing(tmp_path, "pywheels") == sorted([symbols, SDIST])


def test_several_directories_are_pruned_in_one_call(prune, tmp_path):
    proc = prune(
        "pywheels",
        "pywheels-patched",
        contents={"pywheels": CP315_WHEELS + [SDIST], "pywheels-patched": CP315_WHEELS},
    )

    assert proc.returncode == 0, proc.stderr
    assert _listing(tmp_path, "pywheels") == [SDIST]
    assert _listing(tmp_path, "pywheels-patched") == []


def test_an_empty_directory_is_accepted(prune, tmp_path):
    proc = prune("pywheels")

    assert proc.returncode == 0, proc.stderr


def test_a_missing_directory_is_a_hard_error(prune, tmp_path):
    shell = shutil.which("bash")
    proc = subprocess.run(
        [shell, str(_PRUNE_SCRIPT), "pywheeels"],
        cwd=str(tmp_path),
        capture_output=True,
        text=True,
        check=False,
    )

    assert proc.returncode == 1
    assert "refusing to publish unpruned wheels" in proc.stderr


def test_no_arguments_is_a_hard_error(prune, tmp_path):
    shell = shutil.which("bash")
    proc = subprocess.run([shell, str(_PRUNE_SCRIPT)], cwd=str(tmp_path), capture_output=True, text=True, check=False)

    assert proc.returncode == 1
    assert "Usage:" in proc.stderr


def test_the_prune_script_is_executable():
    assert os.access(_PRUNE_SCRIPT, os.X_OK)


def test_pypi_and_adms_publishers_call_the_prune_script() -> None:
    publishers: set[str] = {
        str(path.relative_to(_REPO_ROOT))
        for path, text in _gitlab_text_files()
        if any(command in text for command in PYPI_ADMS_COMMANDS)
    }

    assert publishers == KNOWN_PYPI_ADMS_PUBLISHERS
    for name in sorted(publishers):
        assert "prune-unsupported-wheels.sh" in (_REPO_ROOT / name).read_text()


def test_release_pypi_prunes_before_twine() -> None:
    body: str = _script_body(_RELEASE_YML, "release_pypi_prod")

    _assert_prunes_before_publishing(body, "twine check")
    _assert_prunes_before_publishing(body, "twine upload")


def test_s3_upload_does_not_prune() -> None:
    text: str = _UPLOAD_TO_S3.read_text()
    assert "aws s3 cp" in text
    assert "prune-unsupported-wheels.sh" not in text


@pytest.mark.parametrize("job", ["ddtrace package", "ddtrace package serverless"])
def test_ddtrace_package_does_not_prune(job: str) -> None:
    body: str = _script_body(_PACKAGE_YML, job)
    assert "prune-unsupported-wheels.sh" not in body
    assert "validate-ddtrace-package.py" in body


def test_patch_wheel_versions_prunes_before_the_prerelease_upload() -> None:
    body: str = _script_body(_PACKAGE_YML, "patch manylinux2014 wheel versions")

    _assert_prunes_before_publishing(body, "patch-wheel-versions.py")
    _assert_prunes_before_publishing(body, "adms first-party python upload")
