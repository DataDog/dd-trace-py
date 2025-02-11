import subprocess


def test_pytest_plugin_and_gevent(tmpdir):
    """
    The following is a reproduction case for #8281.

    When running pytest with gevent patched, the ddtrace pytest plugin causes a deadlock
    or error because of an unreleased lock.

    The cause was eager loading the telemetry writer in ddtrace/__init__.py which gets
    loaded by pytest when loading the entrypoints for our plugin.
    """

    test_code = """
import os
import ddtrace
from ddtrace.internal.core import crashtracking
from ddtrace.settings.crashtracker import config
config.debug_url = "file://" + os.environ["CI_PROJECT_DIR"] + "/crashtracker.log"
config.stderr_filename = os.environ["CI_PROJECT_DIR"] + "/crashtracker.stderr.log"
config.stdout_filename = os.environ["CI_PROJECT_DIR"] + "/crashtracker.stdout.log"
crashtracking.start()

import gevent.monkey
gevent.monkey.patch_all()

def test_thing():
    pass
    """

    test_file = tmpdir / "test.py"
    test_file.write(test_code)

    commands_to_test = [
        [
            "pytest",
        ],
        ["pytest", "-p", "no:ddtrace"],
        ["pytest", "-p", "ddtrace"],
        ["pytest", "-p", "ddtrace", "-p", "ddtrace.pytest_bdd", "-p", "ddtrace.pytest_benchmark"],
        ["pytest", "-p", "no:ddtrace", "-p", "no:ddtrace.pytest_bdd", "-p", "no:ddtrace.pytest_benchmark"],
    ]

    for command_args in commands_to_test:
        args = command_args + [str(test_file)]
        result = subprocess.run(
            args,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=True,
        )
        assert result.returncode == 0, result.stderr
