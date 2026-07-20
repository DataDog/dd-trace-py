import os
from pathlib import Path
import shlex
import subprocess

import pytest


PROJECT_ROOT = Path(__file__).parents[2]
DDTEST = PROJECT_ROOT / "scripts" / "ddtest"


def _write_executable(path, content):
    path.write_text(content)
    path.chmod(0o755)


def _run_ddtest(tmp_path, args, podman_compat=False, agent_url=None):
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    calls_file = tmp_path / "docker-calls"

    _write_executable(
        fake_bin / "docker",
        """#!/usr/bin/env bash
printf '%q ' "$@" >> "$DDTEST_CAPTURE_FILE"
printf '\\n' >> "$DDTEST_CAPTURE_FILE"
""",
    )
    _write_executable(
        fake_bin / "sudo",
        """#!/usr/bin/env bash
exec "$@"
""",
    )

    env = os.environ.copy()
    env["DD_TEST_CACHE_DIR"] = str(tmp_path / "cache")
    env["DDTEST_CAPTURE_FILE"] = str(calls_file)
    env["PATH"] = f"{fake_bin}{os.pathsep}{env['PATH']}"
    env.pop("DD_TEST_PODMAN_COMPAT", None)
    env.pop("DD_TRACE_AGENT_URL", None)
    if podman_compat:
        env["DD_TEST_PODMAN_COMPAT"] = "1"
    if agent_url is not None:
        env["DD_TRACE_AGENT_URL"] = agent_url

    result = subprocess.run(
        [str(DDTEST), *args],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    return [shlex.split(line) for line in calls_file.read_text().splitlines()]


def test_ddtest_preserves_docker_run_arguments(tmp_path):
    calls = _run_ddtest(
        tmp_path,
        ["--build", "riot", "run", "--", "-k", "first test or second test"],
    )

    assert len(calls) == 1
    run_index = calls[0].index("run")
    assert calls[0][run_index : run_index + 7] == [
        "run",
        "--build",
        "-e",
        "DD_TRACE_AGENT_URL",
        "--rm",
        "-i",
        "testrunner",
    ]
    command = calls[0][-1].split(" && ", 1)[1]
    assert shlex.split(command) == ["riot", "run", "--", "-k", "first test or second test"]


@pytest.mark.parametrize(
    ("agent_url", "expected_env"),
    [
        (None, None),
        ("", "DD_TRACE_AGENT_URL="),
        (
            "http://testagent:9126/path with spaces",
            "DD_TRACE_AGENT_URL=http://testagent:9126/path with spaces",
        ),
    ],
)
def test_ddtest_podman_mode_uses_compatible_run_arguments(
    tmp_path, agent_url, expected_env
):
    calls = _run_ddtest(tmp_path, ["true"], podman_compat=True, agent_url=agent_url)

    assert len(calls) == 1
    run_index = calls[0].index("run")
    expected_run_args = ["run"]
    if expected_env is not None:
        expected_run_args.extend(["-e", expected_env])
    expected_run_args.extend(["--rm", "testrunner", "bash", "-c"])

    assert calls[0][run_index : run_index + len(expected_run_args)] == expected_run_args
    assert "-i" not in calls[0]
    assert "--build" not in calls[0]


def test_ddtest_podman_mode_builds_before_running(tmp_path):
    calls = _run_ddtest(tmp_path, ["--build", "true"], podman_compat=True)

    assert len(calls) == 2
    assert calls[0][-2:] == ["build", "testrunner"]
    run_index = calls[1].index("run")
    assert calls[1][run_index : run_index + 3] == ["run", "--rm", "testrunner"]
    assert "--build" not in calls[1]
    assert "-i" not in calls[1]


def test_ddtest_rejects_invalid_podman_mode():
    env = os.environ.copy()
    env["DD_TEST_PODMAN_COMPAT"] = "unexpected"

    result = subprocess.run(
        [str(DDTEST), "true"],
        cwd=PROJECT_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 2
    assert result.stdout == "Error: DD_TEST_PODMAN_COMPAT must be set to 0 or 1.\n"
