"""Unit tests for the sccache pre-start in setup.py's interpose_sccache()."""

import os
from pathlib import Path
import shutil
import subprocess

import pytest


SETUP_PY = Path(__file__).resolve().parents[2] / "setup.py"


def _interpose_sccache():
    source = SETUP_PY.read_text()
    code = source[source.index("SCCACHE_START_MAX_ATTEMPTS") : source.index("def retry_download(")]
    namespace = {
        "os": os,
        "shutil": shutil,
        "subprocess": subprocess,
        "Path": Path,
        "SCCACHE_COMPILE": True,
        "time": type("NoSleep", (), {"sleep": staticmethod(lambda _: None)}),
    }
    exec(code, namespace)  # noqa: S102
    return namespace["interpose_sccache"], namespace["SCCACHE_START_MAX_ATTEMPTS"]


def _fake_sccache(tmp_path, failures):
    """
    A sccache stand-in that fails the first `failures` calls with a reason on stderr.

    Like the real binary, --start-server always fails while a `running` file exists (address in use), and
    --show-stats reports success even when no server can start.
    """
    counter = tmp_path / "attempts"
    script = tmp_path / "sccache"
    script.write_text(
        "#!/bin/sh\n"
        '[ "$1" = "--show-stats" ] && exit 0\n'
        f'[ "$1" = "--start-server" ] && [ -e "{tmp_path / "running"}" ] && exit 2\n'
        f'n=$(cat "{counter}" 2>/dev/null || echo 0)\n'
        "n=$((n+1))\n"
        f'echo $n > "{counter}"\n'
        f'[ "$n" -le {failures} ] && {{ echo "backend-unreachable" >&2; exit 2; }}\n'
        "exit 0\n"
    )
    script.chmod(0o755)
    return script, counter


@pytest.fixture
def clean_env(monkeypatch):
    for var in ("RUSTC_WRAPPER", "DD_SCCACHE_PATH", "DD_CC_OLD", "DD_CXX_OLD"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("CC", "cc")
    monkeypatch.setenv("CXX", "c++")
    return monkeypatch


def test_retries_start_then_interposes(tmp_path, clean_env):
    interpose, max_attempts = _interpose_sccache()
    script, counter = _fake_sccache(tmp_path, failures=max_attempts - 1)
    clean_env.setenv("SCCACHE_PATH", str(script))

    interpose()

    assert int(counter.read_text()) == max_attempts
    assert os.environ["RUSTC_WRAPPER"] == str(script.resolve())


def test_disables_sccache_when_start_keeps_failing(tmp_path, clean_env):
    interpose, max_attempts = _interpose_sccache()
    script, counter = _fake_sccache(tmp_path, failures=max_attempts + 10)
    clean_env.setenv("SCCACHE_PATH", str(script))

    interpose()

    assert int(counter.read_text()) == max_attempts
    for var in ("RUSTC_WRAPPER", "DD_SCCACHE_PATH", "DD_CC_OLD", "DD_CXX_OLD"):
        assert var not in os.environ
    assert os.environ["CC"] == "cc"
    assert os.environ["CXX"] == "c++"


def test_reuses_server_that_is_already_running(tmp_path, clean_env):
    interpose, _ = _interpose_sccache()
    script, _ = _fake_sccache(tmp_path, failures=0)
    (tmp_path / "running").touch()
    clean_env.setenv("SCCACHE_PATH", str(script))

    interpose()

    assert os.environ["RUSTC_WRAPPER"] == str(script.resolve())


def test_warning_includes_sccache_output(tmp_path, clean_env, capsys):
    interpose, max_attempts = _interpose_sccache()
    script, _ = _fake_sccache(tmp_path, failures=max_attempts)
    clean_env.setenv("SCCACHE_PATH", str(script))

    interpose()

    assert "backend-unreachable" in capsys.readouterr().out
