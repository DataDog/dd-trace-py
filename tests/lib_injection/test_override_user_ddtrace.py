import json
import os
import subprocess

import pytest


# A marker baked into the stub "user-installed" ddtrace so we can tell which package
# actually got imported by the application.
USER_STUB_VERSION = "0.0.0-user-stub"


def _make_user_ddtrace_stub(stub_root):
    """Create a minimal stub ``ddtrace`` package to simulate a user-installed copy.

    The stub lives in its own directory tree so it can be placed on ``PYTHONPATH``
    independently of the injected (bundled) ddtrace site-packages. It carries a
    distinctive ``__version__`` so tests can assert which package was imported.

    Returns the directory that should be added to ``PYTHONPATH``.
    """
    stub_pkg_dir = os.path.join(stub_root, "ddtrace")
    os.makedirs(stub_pkg_dir, exist_ok=True)
    with open(os.path.join(stub_pkg_dir, "__init__.py"), "w") as f:
        f.write("__version__ = %r\n" % USER_STUB_VERSION)
    return stub_root


# Script that reports which ddtrace package the application ended up importing.
REPORT_DDTRACE_SCRIPT = """
import ddtrace

print("LOADED_DDTRACE_FILE=" + str(getattr(ddtrace, "__file__", "unknown")))
print("LOADED_DDTRACE_VERSION=" + str(getattr(ddtrace, "__version__", "unknown")))
"""


# Script that imports ddtrace (triggering the injected sitecustomize to rewrite PYTHONPATH for
# children), then spawns a child interpreter inheriting that environment and reports which
# ddtrace package the *child* resolves. This exercises the subprocess code path that rewrites
# os.environ["PYTHONPATH"].
SPAWN_CHILD_SCRIPT = """
import os
import subprocess
import sys

import ddtrace

child = subprocess.run(
    [
        sys.executable,
        "-c",
        "import ddtrace; print('CHILD_DDTRACE_FILE=' + str(getattr(ddtrace, '__file__', 'unknown')))",
    ],
    capture_output=True,
    text=True,
    env=os.environ.copy(),
    timeout=120,
)
print("PARENT_DDTRACE_FILE=" + str(getattr(ddtrace, "__file__", "unknown")))
sys.stdout.write(child.stdout)
sys.stderr.write(child.stderr)
"""


def _run_app(python_executable, env, venv_dir):
    return subprocess.run(
        [python_executable, "-c", REPORT_DDTRACE_SCRIPT],
        capture_output=True,
        text=True,
        env=env,
        cwd=venv_dir,
        check=True,
        timeout=180,
    )


def test_override_user_ddtrace_prefers_injected(test_venv, mock_telemetry_forwarder, tmp_path):
    """When DD_INJECT_EXPERIMENTAL_OVERRIDE_USER_DDTRACE is set, the injected (bundled) ddtrace
    takes precedence even though a user-installed ddtrace is present on the path.
    """
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv({})

    user_site = _make_user_ddtrace_stub(str(tmp_path / "user_site"))

    env = base_env.copy()
    venv_pythonpath = base_env.get("PYTHONPATH", "")
    # Order: sitecustomize first, then the user stub, then the venv's own path. The stub
    # appears *before* any injection, so only the override flag should cause it to be skipped.
    env["PYTHONPATH"] = os.pathsep.join([sitecustomize_dir, user_site, venv_pythonpath])
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_INJECTION_ENABLED"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    env["DD_INJECT_FORCE"] = "false"
    env["DD_INJECT_EXPERIMENTAL_OVERRIDE_USER_DDTRACE"] = "true"
    telemetry_file = mock_telemetry_forwarder(env, python_executable)

    try:
        result = _run_app(python_executable, env, venv_dir)
        stderr = result.stderr
        stdout = result.stdout

        # The override path logs a distinct message instead of "user-installed ddtrace not found".
        assert "DD_INJECT_EXPERIMENTAL_OVERRIDE_USER_DDTRACE is set, preferring injection site-packages" in stderr, (
            f"override log message not found. stderr: {stderr}"
        )

        # The injected (bundled) ddtrace was imported, NOT the user stub.
        file_line = next((line for line in stdout.splitlines() if line.startswith("LOADED_DDTRACE_FILE=")), "")
        assert "ddtrace_pkgs" in file_line, f"expected injected ddtrace to be loaded, got: {file_line!r}"
        assert "user_site" not in file_line, f"user-installed ddtrace was loaded instead of injected: {file_line!r}"
        assert ("LOADED_DDTRACE_VERSION=" + USER_STUB_VERSION) not in stdout, (
            f"user stub ddtrace was loaded, stdout: {stdout}"
        )

        # Telemetry reflects a successful injection, not an abort.
        assert telemetry_file.is_file()
        telemetry_data = json.loads(telemetry_file.read_text())
        assert telemetry_data["metadata"]["result"] == "success"
        assert telemetry_data["metadata"]["result_class"] == "success"
        points = telemetry_data["points"]
        assert len(points) == 1
        complete_metric = points[0]
        assert complete_metric["name"] == "library_entrypoint.complete"
        assert "injection_forced:false" in complete_metric["tags"]

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed:\nExit Code: {e.returncode}\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")
    except subprocess.TimeoutExpired as e:
        pytest.fail(f"Subprocess timed out:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_user_ddtrace_present_without_override_aborts(test_venv, mock_telemetry_forwarder, tmp_path):
    """Without the override flag, an existing user-installed ddtrace wins and injection aborts.

    This is the contrast case for ``test_override_user_ddtrace_prefers_injected`` and
    documents the default precedence behavior.
    """
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv({})

    user_site = _make_user_ddtrace_stub(str(tmp_path / "user_site"))

    env = base_env.copy()
    venv_pythonpath = base_env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = os.pathsep.join([sitecustomize_dir, user_site, venv_pythonpath])
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_INJECTION_ENABLED"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    env["DD_INJECT_FORCE"] = "false"
    # DD_INJECT_EXPERIMENTAL_OVERRIDE_USER_DDTRACE deliberately unset.
    telemetry_file = mock_telemetry_forwarder(env, python_executable)

    try:
        result = _run_app(python_executable, env, venv_dir)
        stderr = result.stderr
        stdout = result.stdout

        # Injection detects the user-installed ddtrace and aborts.
        assert "user-installed ddtrace found" in stderr, f"expected abort log, stderr: {stderr}"
        assert "DD_INJECT_EXPERIMENTAL_OVERRIDE_USER_DDTRACE is set" not in stderr

        # The user stub is what the application imported.
        file_line = next((line for line in stdout.splitlines() if line.startswith("LOADED_DDTRACE_FILE=")), "")
        assert "user_site" in file_line, f"expected user-installed ddtrace to be loaded, got: {file_line!r}"
        assert ("LOADED_DDTRACE_VERSION=" + USER_STUB_VERSION) in stdout, (
            f"expected user stub ddtrace to be loaded, stdout: {stdout}"
        )

        # Telemetry reports the abort with the already-instrumented classification.
        assert telemetry_file.is_file()
        telemetry_data = json.loads(telemetry_file.read_text())
        assert telemetry_data["metadata"]["result"] == "abort"
        assert telemetry_data["metadata"]["result_class"] == "already_instrumented"
        points = telemetry_data["points"]
        assert len(points) == 1
        abort_metric = points[0]
        assert abort_metric["name"] == "library_entrypoint.abort"
        assert "reason:ddtrace_already_present" in abort_metric["tags"]

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed:\nExit Code: {e.returncode}\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")
    except subprocess.TimeoutExpired as e:
        pytest.fail(f"Subprocess timed out:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")


def test_override_user_ddtrace_prefers_injected_in_child_process(test_venv, mock_telemetry_forwarder, tmp_path):
    """The override precedence is propagated to spawned child processes via PYTHONPATH.

    With a user-installed ddtrace reachable through PYTHONPATH, the rewritten PYTHONPATH must
    place the injected site-packages ahead of the user entry so that a child interpreter (which
    rebuilds sys.path from PYTHONPATH) also resolves ``import ddtrace`` to the injected package.
    """
    python_executable, sitecustomize_dir, base_env, venv_dir = test_venv({})

    user_site = _make_user_ddtrace_stub(str(tmp_path / "user_site"))

    env = base_env.copy()
    venv_pythonpath = base_env.get("PYTHONPATH", "")
    # The user stub is reachable via PYTHONPATH (not just site-packages); this is the configuration
    # in which a tail-appended injected path would lose to the user copy in a child interpreter.
    env["PYTHONPATH"] = os.pathsep.join([sitecustomize_dir, user_site, venv_pythonpath])
    env["DD_TRACE_DEBUG"] = "true"
    env["DD_INJECTION_ENABLED"] = "true"
    env["DD_TRACE_AGENT_URL"] = "http://localhost:9126"
    env["DD_INJECT_FORCE"] = "false"
    env["DD_INJECT_EXPERIMENTAL_OVERRIDE_USER_DDTRACE"] = "true"
    mock_telemetry_forwarder(env, python_executable)

    try:
        result = subprocess.run(
            [python_executable, "-c", SPAWN_CHILD_SCRIPT],
            capture_output=True,
            text=True,
            env=env,
            cwd=venv_dir,
            check=True,
            timeout=180,
        )
        stdout = result.stdout

        # Both the parent and the spawned child resolve the injected (bundled) ddtrace.
        parent_line = next((line for line in stdout.splitlines() if line.startswith("PARENT_DDTRACE_FILE=")), "")
        child_line = next((line for line in stdout.splitlines() if line.startswith("CHILD_DDTRACE_FILE=")), "")

        assert "ddtrace_pkgs" in parent_line, f"parent did not load injected ddtrace: {parent_line!r}"
        assert child_line, f"child did not report a ddtrace path. stdout: {stdout}"
        assert "ddtrace_pkgs" in child_line, f"child did not load injected ddtrace: {child_line!r}"
        assert "user_site" not in child_line, f"child loaded user-installed ddtrace: {child_line!r}"

    except subprocess.CalledProcessError as e:
        pytest.fail(f"Subprocess failed:\nExit Code: {e.returncode}\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")
    except subprocess.TimeoutExpired as e:
        pytest.fail(f"Subprocess timed out:\nstdout:\n{e.stdout}\nstderr:\n{e.stderr}")
