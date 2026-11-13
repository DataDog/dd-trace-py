"""Test cache startup and wheel logging without importing the native build backend."""

import ast
import contextlib
import io
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
HELPERS = ROOT / ".gitlab" / "scripts" / "build-wheel-helpers.sh"


def load_setup_sccache():
    # Importing setup.py would configure and build native extensions. Execute the
    # actual cache helpers in isolation so these tests need only the standard library.
    tree = ast.parse((ROOT / "setup.py").read_text())
    names = {"start_sccache_server", "interpose_sccache"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in names]
    namespace = {
        "os": os,
        "Path": Path,
        "shutil": shutil,
        "subprocess": subprocess,
        "sys": sys,
        "time": time,
        "SCCACHE_COMPILE": True,
    }
    nodes += [
        node
        for node in tree.body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id.startswith("SCCACHE_START_") for target in node.targets)
    ]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / "setup.py"), "exec"), namespace)
    return namespace


class SetupSccacheTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        for name in ("sccache", "cc", "c++"):
            executable = self.root / name
            executable.write_text("#!/bin/sh\nexit 0\n")
            executable.chmod(0o755)
        self.environment = {
            "PATH": str(self.root),
            "SCCACHE_PATH": str(self.root / "sccache"),
            "CC": "cc",
            "CXX": "c++",
        }
        self.namespace = load_setup_sccache()
        self.stderr = io.StringIO()
        self.namespace["sys"] = types.SimpleNamespace(argv=["setup.py", "build_ext"], stderr=self.stderr)
        self.addCleanup(mock.patch.stopall)
        mock.patch.dict(os.environ, self.environment, clear=True).start()
        self.run = mock.patch.object(subprocess, "run", return_value=subprocess.CompletedProcess([], 0)).start()
        self.sleep = mock.patch.object(time, "sleep").start()

    def test_hashing_does_not_contact_cache_or_change_compilers(self):
        self.namespace["sys"].argv = ["setup.py", "ext_hashes", "--inplace"]
        before = dict(os.environ)
        self.namespace["interpose_sccache"]()
        self.run.assert_not_called()
        self.sleep.assert_not_called()
        self.assertEqual(dict(os.environ), before)
        self.assertEqual(self.stderr.getvalue(), "")

    def test_disabled_cache_does_not_probe(self):
        self.namespace["SCCACHE_COMPILE"] = False
        self.namespace["interpose_sccache"]()
        self.run.assert_not_called()

    def test_healthy_build_enables_compiler_wrappers(self):
        self.namespace["interpose_sccache"]()
        self.run.assert_called_once_with(
            [str(self.root / "sccache"), "--dist-status"],
            check=True,
            capture_output=True,
            text=True,
            timeout=60,
        )
        self.sleep.assert_not_called()
        self.assertEqual(os.environ["RUSTC_WRAPPER"], str(self.root / "sccache"))
        self.assertEqual(os.environ["DD_SCCACHE_PATH"], str(self.root / "sccache"))
        self.assertEqual(os.environ["CC"], f"{self.root / 'sccache'} {self.root / 'cc'}")
        self.assertEqual(os.environ["CXX"], f"{self.root / 'sccache'} {self.root / 'c++'}")
        self.assertIn("sccache server ready after", self.stderr.getvalue())

    def test_failed_checks_keep_retry_policy_and_fall_back(self):
        self.run.side_effect = subprocess.CalledProcessError(1, ["sccache"], stderr="backend unavailable")
        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            self.namespace["interpose_sccache"]()
        self.assertEqual(self.run.call_count, 3)
        self.assertEqual(self.sleep.call_args_list, [mock.call(2), mock.call(4)])
        self.assertNotIn("RUSTC_WRAPPER", os.environ)
        self.assertEqual(os.environ["CC"], "cc")
        self.assertEqual(os.environ["CXX"], "c++")
        self.assertEqual(stdout.getvalue(), "")
        self.assertEqual(self.stderr.getvalue().count("backend unavailable"), 3)
        self.assertIn("building without sccache", self.stderr.getvalue())

    def test_transient_failure_recovers(self):
        self.run.side_effect = [
            subprocess.CalledProcessError(1, ["sccache"], stderr="temporary failure"),
            subprocess.CompletedProcess([], 0),
        ]
        self.namespace["interpose_sccache"]()
        self.assertEqual(self.run.call_count, 2)
        self.sleep.assert_called_once_with(2)
        self.assertIn("RUSTC_WRAPPER", os.environ)
        self.assertNotIn("building without sccache", self.stderr.getvalue())

    def test_timeout_reports_elapsed_time_and_decodes_stderr(self):
        self.run.side_effect = subprocess.TimeoutExpired(["sccache"], 60, stderr=b"connection timed out")
        with mock.patch.object(time, "monotonic", side_effect=[0, 60, 62, 122, 126, 186]):
            self.assertFalse(self.namespace["start_sccache_server"](self.root / "sccache"))
        self.assertEqual(self.stderr.getvalue().count("after 60.0s: connection timed out"), 3)
        self.assertNotIn("b'connection", self.stderr.getvalue())


class WheelLoggingTests(unittest.TestCase):
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.root = Path(directory.name)
        self.built = self.root / "built"
        self.built.mkdir()
        self.logs = self.root / "logs"
        self.logs.mkdir()
        self.release = self.root / "release"
        uv = self.root / "uv"
        uv.write_text(
            "#!/bin/bash\n"
            'if [ "$1" = run ]; then\n'
            '  case "$*" in *BUILD_GNU_TYPE*) echo cp312-test ;; *) echo 3.12 ;; esac\n'
            "else\n"
            '  echo "ordinary compiler output"\n'
            '  echo "WARNING: sccache server unavailable (attempt 1/3) after 60.0s: backend unavailable" >&2\n'
            '  if [ "$WAIT_FOR_RELEASE" = 1 ]; then\n'
            '    while [ ! -f "$RELEASE_FILE" ]; do sleep 0.01; done\n'
            "  fi\n"
            '  touch "$BUILT_WHEEL_DIR/test.whl"\n'
            '  exit "$BUILD_STATUS"\n'
            "fi\n"
        )
        uv.chmod(0o755)
        self.environment = {
            **os.environ,
            "PATH": f"{self.root}{os.pathsep}{os.environ.get('PATH', '')}",
            "DEBUG_WHEEL_DIR": str(self.logs),
            "BUILT_WHEEL_DIR": str(self.built),
            "RELEASE_FILE": str(self.release),
            "WAIT_FOR_RELEASE": "0",
            "BUILD_STATUS": "0",
        }
        self.command = ["/bin/bash", "-c", 'source "$1"\nbuild_wheel', "wheel-test", str(HELPERS)]

    def test_success_surfaces_cache_health_and_preserves_full_log(self):
        result = subprocess.run(self.command, env=self.environment, capture_output=True, text=True, timeout=3)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("sccache server unavailable", result.stdout)
        self.assertNotIn("ordinary compiler output", result.stdout)
        log = next(self.logs.glob("build_*.log")).read_text()
        self.assertIn("ordinary compiler output", log)
        self.assertIn("sccache server unavailable", log)

    def test_failure_is_not_hidden_by_log_pipeline(self):
        result = subprocess.run(
            self.command, env={**self.environment, "BUILD_STATUS": "7"}, capture_output=True, text=True, timeout=3
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Build failed", result.stdout)
        self.assertIn("ordinary compiler output", result.stdout)
        self.assertNotIn("Build completed successfully", result.stdout)

    def test_cache_warning_is_visible_while_build_is_running(self):
        process = subprocess.Popen(
            self.command,
            env={**self.environment, "WAIT_FOR_RELEASE": "1"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        output = b""
        try:
            with selectors.DefaultSelector() as selector:
                selector.register(process.stdout, selectors.EVENT_READ)
                deadline = time.monotonic() + 3
                while time.monotonic() < deadline and b"sccache server unavailable" not in output:
                    if selector.select(timeout=0.1):
                        chunk = os.read(process.stdout.fileno(), 4096)
                        if not chunk:
                            break
                        output += chunk
            self.assertIn(b"sccache server unavailable", output)
            self.assertIsNone(process.poll())
        finally:
            self.release.touch()
            remainder, _ = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, (output + remainder).decode())


if __name__ == "__main__":
    unittest.main()
