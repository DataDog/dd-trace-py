#!/usr/bin/env python3
"""Tests for lib-injection/dl_wheels.py.

prepare-oci-package.sh runs dl_wheels.py with the system python3 of the
packaging image. That interpreter has pip but no third-party modules, so the
tests run the script with site-packages disabled.
"""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


_SCRIPT = Path(__file__).resolve().parents[2] / "lib-injection" / "dl_wheels.py"


def _run_dry(cwd, *args):
    return subprocess.run(
        [sys.executable, "-I", "-S", str(_SCRIPT), "--dry-run", "--local-ddtrace", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
    )


class DlWheelsTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.wheels = Path(tmp.name) / "pywheels"
        self.wheels.mkdir()
        self.out = Path(tmp.name) / "out"
        self.out.mkdir()
        for abi in ("cp39", "cp314"):
            for platform in ("manylinux_2_17_x86_64.manylinux2014_x86_64", "musllinux_1_2_x86_64"):
                (self.wheels / f"ddtrace-1.0.0-{abi}-{abi}-{platform}.whl").touch()

    def test_runs_without_site_packages(self):
        result = _run_dry(
            self.wheels,
            "--python-version=3.9",
            "--arch=x86_64",
            "--platform=manylinux2014",
            f"--output-dir={self.out}",
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_one_call_covers_every_version_and_platform(self):
        result = _run_dry(
            self.wheels,
            "--python-version=3.9",
            "--python-version=3.14",
            "--arch=x86_64",
            "--platform=manylinux2014",
            "--platform=musllinux_1_2",
            f"--output-dir={self.out}",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            sorted(line for line in result.stdout.splitlines() if line.startswith("Downloading ")),
            [
                "Downloading 3.14 x86_64 manylinux2014 wheel",
                "Downloading 3.14 x86_64 musllinux_1_2 wheel",
                "Downloading 3.9 x86_64 manylinux2014 wheel",
                "Downloading 3.9 x86_64 musllinux_1_2 wheel",
            ],
        )


if __name__ == "__main__":
    unittest.main()
