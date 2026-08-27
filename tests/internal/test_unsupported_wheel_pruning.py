"""Tests for .gitlab/scripts/prune-unsupported-wheels.sh and the jobs that call it.

cp315 wheels are built under allow_failure so that Python 3.15 keeps producing CI signal,
but they are ABI-broken and must not reach any channel outside the pipeline. Three
mechanisms publish ddtrace wheels: twine to PyPI, aws s3 cp to the anonymously readable
dd-trace-py-builds bucket, and adms to the pypi-private-prereleases index. All three call
the prune script first.

Publication jobs only run on release tags or on main, so a PR pipeline never executes their
shell. These tests run the prune script directly against fixture directories and read the
wiring out of the shipped YAML and shell, so the assertions cannot drift from CI.

What is being pinned:
  * cp315 wheels are deleted; every other wheel, the sdist and debug symbol archives survive.
  * A directory that does not exist is a hard error, so a typo in a call site fails the job
    instead of silently publishing an unpruned directory.
  * Every publish command under .gitlab/ lives in a file that also calls the prune script.
    This is what stops a new upload path from quietly reintroducing cp315.
  * Each known call site prunes before it publishes, not after.
"""

import os
import pathlib
import shutil
import subprocess

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

# Commands that hand a wheel to something outside this pipeline. Anything under .gitlab/
# that runs one of these must also call the prune script.
PUBLISH_COMMANDS = ("twine upload", "aws s3 cp", "adms first-party python upload")

# The files that are allowed to publish today. Listed explicitly so that adding a fourth
# publication mechanism is a deliberate, reviewed edit rather than a silent one.
KNOWN_PUBLISHERS = {
    ".gitlab/package.yml",
    ".gitlab/release.yml",
    ".gitlab/scripts/upload-wheels-to-s3.sh",
}


class _GitLabLoader(yaml.SafeLoader):
    """SafeLoader that tolerates GitLab's !reference tag."""


_GitLabLoader.add_constructor("!reference", lambda loader, node: None)


def _resolve(doc: dict, job: str, key: str):
    """Walk the extends: chain until key is found, the way GitLab does."""
    while job is not None:
        if key in doc[job]:
            return doc[job][key]
        job = doc[job].get("extends")
    raise AssertionError(f"{key!r} not found in the extends chain of the job")


def _script_body(path: pathlib.Path, job: str) -> str:
    """A job's script: block, joined as GitLab joins it."""
    doc = yaml.load(path.read_text(), Loader=_GitLabLoader)
    script = _resolve(doc, job, "script")
    assert isinstance(script, list), f"{job} script: is expected to be a list"
    return "\n".join(entry for entry in script if isinstance(entry, str))


def _gitlab_text_files():
    for path in sorted(_GITLAB_DIR.rglob("*")):
        if not path.is_file():
            continue
        try:
            yield path, path.read_text()
        except UnicodeDecodeError:
            continue


def _assert_prunes_before_publishing(body: str, publish_command: str) -> None:
    prune_at = body.find("prune-unsupported-wheels.sh")
    publish_at = body.find(publish_command)
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
    """upload serverless is the only publisher of the ddtrace_serverless flavour."""
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
    """Debug symbols are not installable, so they are deliberately out of scope."""
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
    """A typo in a call site must fail the job, not skip the prune."""
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
    """Call sites invoke it directly rather than through an interpreter."""
    assert os.access(_PRUNE_SCRIPT, os.X_OK)


def test_every_publisher_under_gitlab_calls_the_prune_script():
    """The invariant that stops a new upload path from reintroducing cp315."""
    publishers = {
        str(path.relative_to(_REPO_ROOT))
        for path, text in _gitlab_text_files()
        if any(command in text for command in PUBLISH_COMMANDS)
    }

    assert publishers == KNOWN_PUBLISHERS, (
        "a file under .gitlab/ gained or lost a publish command; if it publishes wheels it "
        "must call .gitlab/scripts/prune-unsupported-wheels.sh first"
    )
    for name in sorted(publishers):
        assert "prune-unsupported-wheels.sh" in (_REPO_ROOT / name).read_text(), (
            f"{name} runs a publish command without calling the prune script"
        )


def test_release_pypi_prunes_before_twine():
    body = _script_body(_RELEASE_YML, "release_pypi_prod")

    _assert_prunes_before_publishing(body, "twine check")
    _assert_prunes_before_publishing(body, "twine upload")


def test_upload_wheels_to_s3_prunes_before_the_first_upload():
    _assert_prunes_before_publishing(_UPLOAD_TO_S3.read_text(), "aws s3 cp")


@pytest.mark.parametrize("job", ["ddtrace package", "ddtrace package serverless"])
def test_ddtrace_package_prunes_before_validating(job):
    """The artifact release_pypi_prod and 'upload all' inherit must already be clean."""
    _assert_prunes_before_publishing(_script_body(_PACKAGE_YML, job), "validate-ddtrace-package.py")


def test_patch_wheel_versions_prunes_before_the_prerelease_upload():
    body = _script_body(_PACKAGE_YML, "patch manylinux2014 wheel versions")

    _assert_prunes_before_publishing(body, "patch-wheel-versions.py")
    _assert_prunes_before_publishing(body, "adms first-party python upload")
