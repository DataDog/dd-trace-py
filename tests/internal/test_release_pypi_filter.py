"""Tests for the PyPI upload filter in .gitlab/release.yml.

release_pypi_prod only runs on release tags (.is_release), so a PR pipeline never executes
its shell. These tests pull the script: block straight out of the YAML and run it against
fixture directories with aws and uvx stubbed on PATH, so the assertions cannot drift from
the shipped config.

What is being pinned:
  * cp315 wheels are withheld from PyPI while Python 3.15 is unsupported; everything else,
    sdist included, is still uploaded.
  * twine check and twine upload see the same list. cp315 builds are allow_failure, so a
    malformed cp315 wheel must not fail a release whose supported wheels are all fine.
  * An empty list is a hard error rather than a silent no-op.
"""

import os
import pathlib
import shutil
import stat
import subprocess

import pytest
import yaml


_REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_RELEASE_YML = _REPO_ROOT / ".gitlab" / "release.yml"

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
# "build linux" in .gitlab/package.yml is the only job whose matrix carries cp315-cp315,
# and it covers manylinux2014 + musllinux on amd64 + arm64. macOS and Windows stop at 3.14.
CP315_PLATFORMS = [
    "manylinux2014_aarch64.manylinux_2_17_aarch64",
    "manylinux2014_x86_64.manylinux_2_17_x86_64",
    "musllinux_1_2_aarch64",
    "musllinux_1_2_x86_64",
]

SUPPORTED_WHEELS = [
    f"ddtrace-{VERSION}-{tag}-{tag}-{platform}.whl" for tag in SUPPORTED_TAGS for platform in BASE_PLATFORMS
] + [f"ddtrace-{VERSION}-{tag}-{tag}-win_arm64.whl" for tag in WIN_ARM64_TAGS]
CP315_WHEELS = [f"ddtrace-{VERSION}-cp315-cp315-{platform}.whl" for platform in CP315_PLATFORMS]
SDIST = f"ddtrace-{VERSION}.tar.gz"

ERROR_MESSAGE = "[ERROR] no PyPI-eligible distributions found in pywheels/ -- refusing to upload"


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


def _merged_variables(doc: dict, job: str) -> dict:
    chain = []
    while job is not None:
        chain.append(job)
        job = doc[job].get("extends")
    merged = {}
    for name in reversed(chain):
        merged.update(doc[name].get("variables") or {})
    return merged


@pytest.fixture(scope="module")
def release_job() -> tuple[str, dict]:
    """The release_pypi_prod script, joined as GitLab joins it, plus its variables."""
    doc = yaml.load(_RELEASE_YML.read_text(), Loader=_GitLabLoader)
    script = _resolve(doc, "release_pypi_prod", "script")
    assert isinstance(script, list), "release_pypi_prod script: is expected to be a list"
    body = "\n".join(entry.rstrip("\n") for entry in script)
    # Fail loudly if the YAML is restructured such that we extract the wrong thing.
    assert "cp315" in body, "the extracted script no longer mentions cp315"
    assert "twine upload" in body, "the extracted script no longer uploads"
    return body, _merged_variables(doc, "release_pypi_prod")


@pytest.fixture()
def run_release_job(release_job, tmp_path):
    """Run the extracted script against a fixture pywheels/ directory."""
    body, variables = release_job

    script = tmp_path / "release-job.sh"
    script.write_text(body)

    stubs = tmp_path / "stubs"
    stubs.mkdir()
    calls = tmp_path / "uvx-calls.txt"
    # `aws` stands in for the SSM token lookup; `uvx` records its argv instead of running
    # twine, so the test neither needs credentials nor touches the network.
    (stubs / "aws").write_text("#!/usr/bin/env sh\necho token-stub\n")
    (stubs / "uvx").write_text('#!/usr/bin/env sh\nprintf \'%s\\n\' "$*" >> "$UVX_CALLS"\n')
    for stub in ("aws", "uvx"):
        path = stubs / stub
        path.chmod(path.stat().st_mode | stat.S_IEXEC)

    def run(filenames, directories=()):
        pywheels = tmp_path / "pywheels"
        pywheels.mkdir()
        for name in filenames:
            (pywheels / name).touch()
        for name in directories:
            (pywheels / name).mkdir()

        env = dict(os.environ)
        env.update({str(k): str(v) for k, v in variables.items()})
        env["CI_PROJECT_NAME"] = "dd-trace-py"
        env["UVX_CALLS"] = str(calls)
        env["PATH"] = f"{stubs}{os.pathsep}{env['PATH']}"

        shell = shutil.which("bash") or "sh"
        proc = subprocess.run(
            [shell, str(script)], cwd=str(tmp_path), env=env, capture_output=True, text=True, check=False
        )
        recorded = calls.read_text().splitlines() if calls.exists() else []
        return proc, recorded

    return run


def _argv_after(recorded: list[str], marker: str) -> list[str]:
    """The file arguments handed to twine check / twine upload, or [] if not called."""
    for line in recorded:
        _, _, tail = line.partition(marker)
        if tail:
            return tail.split()
    return []


def _selected(proc: subprocess.CompletedProcess) -> list[str]:
    """The distributions the job itself printed as selected."""
    return [line.strip() for line in proc.stdout.splitlines() if line.startswith("  pywheels/")]


def test_cp315_wheels_are_withheld_and_everything_else_is_uploaded(run_release_job):
    proc, recorded = run_release_job(SUPPORTED_WHEELS + CP315_WHEELS + [SDIST])

    assert proc.returncode == 0, proc.stderr
    uploaded = _argv_after(recorded, "twine upload --repository pypi ")
    # Compared as sets: glob expansion order is locale-dependent and not what we are pinning.
    assert set(uploaded) == {f"pywheels/{name}" for name in SUPPORTED_WHEELS + [SDIST]}
    assert not [name for name in uploaded if "cp315" in name]
    assert f"pywheels/{SDIST}" in uploaded


def test_twine_check_receives_the_filtered_list_not_the_whole_directory(run_release_job):
    """A malformed cp315 wheel must not fail a release it is not part of."""
    proc, recorded = run_release_job(SUPPORTED_WHEELS + CP315_WHEELS + [SDIST])

    checked = _argv_after(recorded, "twine check --strict ")
    uploaded = _argv_after(recorded, "twine upload --repository pypi ")
    assert checked, "twine check was never invoked"
    assert checked == uploaded
    assert not [name for name in checked if "cp315" in name]
    assert "pywheels/*" not in checked


def test_cp315_only_refuses_to_upload(run_release_job):
    proc, recorded = run_release_job(CP315_WHEELS)

    assert proc.returncode == 1
    assert ERROR_MESSAGE in proc.stderr
    assert recorded == [], "twine must not run when nothing is eligible"


def test_empty_directory_refuses_to_upload(run_release_job):
    proc, recorded = run_release_job([])

    assert proc.returncode == 1
    assert ERROR_MESSAGE in proc.stderr
    assert recorded == []
    # The unexpanded glob must not be mistaken for a filename.
    assert "ddtrace-*" not in proc.stdout


def test_filter_is_a_no_op_when_no_cp315_is_present(run_release_job):
    proc, recorded = run_release_job(SUPPORTED_WHEELS + [SDIST])

    assert proc.returncode == 0, proc.stderr
    uploaded = _argv_after(recorded, "twine upload --repository pypi ")
    assert set(uploaded) == {f"pywheels/{name}" for name in SUPPORTED_WHEELS + [SDIST]}


def test_sdist_alone_is_enough_to_upload(run_release_job):
    """build sdist is a non-allow_failure dependency, so the list is never empty."""
    proc, recorded = run_release_job([SDIST])

    assert proc.returncode == 0, proc.stderr
    assert _argv_after(recorded, "twine upload --repository pypi ") == [f"pywheels/{SDIST}"]


def test_sdist_survives_when_only_cp315_wheels_were_built(run_release_job):
    proc, recorded = run_release_job(CP315_WHEELS + [SDIST])

    assert proc.returncode == 0, proc.stderr
    assert _argv_after(recorded, "twine upload --repository pypi ") == [f"pywheels/{SDIST}"]


def test_non_ddtrace_distributions_and_directories_are_skipped(run_release_job):
    proc, recorded = run_release_job(
        SUPPORTED_WHEELS
        + [SDIST, f"ddtrace_serverless-{VERSION}-cp313-cp313-manylinux2014_x86_64.manylinux_2_17_x86_64.whl"],
        directories=["ddtrace-leftover-dir"],
    )

    assert proc.returncode == 0, proc.stderr
    selected = _selected(proc)
    assert len(selected) == len(SUPPORTED_WHEELS) + 1
    assert not [name for name in selected if "ddtrace_serverless" in name]
    assert not [name for name in selected if "leftover-dir" in name]
