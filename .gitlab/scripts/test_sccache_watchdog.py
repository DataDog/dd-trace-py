"""Exercise watchdog timing and cleanup without macOS or a real compiler cache."""

import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest


SCRIPT = Path(__file__).with_name("sccache-watchdog.py")
SPEC = importlib.util.spec_from_file_location("sccache_watchdog", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
watchdog = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(watchdog)


def running(pid: int) -> bool:
    result = subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, text=True)
    return result.returncode == 0 and bool(result.stdout.strip()) and not result.stdout.strip().startswith("Z")


class WatchdogTests(unittest.TestCase):
    def setUp(self) -> None:
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.script = self.root / SCRIPT.name
        shutil.copyfile(SCRIPT, self.script)
        self.helper = self.root / "sccache-diagnostics.sh"
        self.helper.write_text('echo "SNAPSHOT $1"\n')

    def start(self, delay: float) -> subprocess.Popen:
        process = subprocess.Popen(
            [sys.executable, str(self.script), "--delay", str(delay)],
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        self.addCleanup(self.finish, process)
        return process

    @staticmethod
    def finish(process: subprocess.Popen) -> None:
        if process.poll() is None:
            process.kill()
        process.communicate(timeout=3)

    def test_delayed_snapshot_runs_once(self) -> None:
        process = self.start(0.05)
        output, _ = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, output)
        self.assertEqual(output.count("SNAPSHOT stalled"), 1, output)

    def test_snapshot_inside_shell_subshell(self) -> None:
        result = subprocess.run(
            ["bash", "-c", '("$1" "$2" --delay 0.05 & wait)', "watchdog-test", sys.executable, str(self.script)],
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.count("SNAPSHOT stalled"), 1, result.stdout)

    def test_cancel_waiting_watchdog(self) -> None:
        process = self.start(60)
        self.assertIn("watchdog armed", process.stdout.readline())
        process.terminate()
        output, _ = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, output)
        self.assertNotIn("SNAPSHOT", output)

    def test_exit_trap_preserves_build_failure(self) -> None:
        pid_file = self.root / "watchdog.pid"
        result = subprocess.run(
            [
                "bash",
                "-c",
                '"$1" "$2" --delay 60 &\n'
                "sccache_watchdog_pid=$!\n"
                'echo "$sccache_watchdog_pid" > "$3"\n'
                'trap \'kill "$sccache_watchdog_pid" 2>/dev/null || true; '
                'wait "$sccache_watchdog_pid" 2>/dev/null || true\' EXIT\n'
                "exit 37\n",
                "watchdog-test",
                sys.executable,
                str(self.script),
                str(pid_file),
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 37, result.stdout + result.stderr)
        self.assertFalse(running(int(pid_file.read_text())))

    def test_parent_exit_without_trap_stops_watchdog(self) -> None:
        pid_file = self.root / "orphan.pid"
        result = subprocess.run(
            [
                "bash",
                "-c",
                '"$1" "$2" --delay 60 &\necho "$!" > "$3"\nsleep 0.2\n',
                "watchdog-test",
                sys.executable,
                str(self.script),
                str(pid_file),
            ],
            capture_output=True,
            text=True,
            timeout=3,
        )
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertNotIn("SNAPSHOT", result.stdout)
        self.assertFalse(running(int(pid_file.read_text())))

    def make_hanging_probe(self) -> Path:
        pid_file = self.root / "probe.pids"
        self.helper.write_text(f'sleep 60 &\necho "$$ $!" > "{pid_file}"\nwait\n')
        return pid_file

    def assert_probe_stopped(self, pid_file: Path) -> None:
        self.assertTrue(pid_file.exists())
        for pid in map(int, pid_file.read_text().split()):
            self.assertFalse(running(pid), f"probe process {pid} survived cleanup")

    def test_probe_timeout_cleans_up_descendants(self) -> None:
        pid_file = self.make_hanging_probe()
        unrelated = subprocess.Popen(["sleep", "60"])
        self.addCleanup(self.finish, unrelated)
        watchdog.probe(["bash", str(self.helper)], timeout=0.5)
        self.assert_probe_stopped(pid_file)
        self.assertIsNone(unrelated.poll())

    def test_probe_exit_cleans_up_leftover_child(self) -> None:
        pid_file = self.root / "leftover.pid"
        self.helper.write_text(f'sleep 60 &\necho "$!" > "{pid_file}"\n')
        watchdog.probe(["bash", str(self.helper)])
        self.assertFalse(running(int(pid_file.read_text())))

    def test_cancel_during_probe_cleans_up_descendants(self) -> None:
        pid_file = self.make_hanging_probe()
        process = self.start(0)
        deadline = time.monotonic() + 2
        while not pid_file.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        self.assertTrue(pid_file.exists())
        process.terminate()
        output, _ = process.communicate(timeout=3)
        self.assertEqual(process.returncode, 0, output)
        self.assert_probe_stopped(pid_file)


if __name__ == "__main__":
    unittest.main()
