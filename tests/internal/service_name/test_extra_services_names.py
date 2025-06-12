import os
import sys

import pytest


@pytest.mark.skipif(sys.platform in ("win32", "cygwin"), reason="Fork not supported on Windows")
def test_config_extra_service_names_fork(run_python_code_in_subprocess):
    code = f"""
import ddtrace.auto
import ddtrace

import re
import os
import sys
import time

children = []
for i in range(10):
    pid = os.fork()
    if pid == 0:
        # Child process
        ddtrace.config._add_extra_service(f"extra_service_{{i}}")
        time.sleep(0.1)  # Ensure the child has time to save the service
        sys.exit(0)
    else:
        # Parent process
        children.append(pid)

for pid in children:
    os.waitpid(pid, 0)

extra_services = ddtrace.config._get_extra_services()
extra_services.discard("sqlite")  # coverage
assert len(extra_services) == 10, extra_services
assert all(re.match(r"extra_service_\\d+_\\d+", service) for service in extra_services)
"""

    env = os.environ.copy()
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "true"
    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, (stdout, stderr, status)


def test_config_extra_service_names_duplicates(run_python_code_in_subprocess):
    code = """
import ddtrace.auto
import ddtrace
import re
import os
import sys
import time

for _ in range(10):
    ddtrace.config._add_extra_service("extra_service_1")

extra_services = ddtrace.config._get_extra_services()
extra_services.discard("sqlite")  # coverage
assert extra_services == {"extra_service_1"}
    """

    env = os.environ.copy()
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "true"
    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, (stdout, stderr, status)


def test_config_extra_service_names_rc_disabled(run_python_code_in_subprocess):
    code = """
import ddtrace.auto
import ddtrace
import re
import os
import sys
import time

for _ in range(10):
    ddtrace.config._add_extra_service("extra_service_1")

extra_services = ddtrace.config._get_extra_services()
assert len(extra_services) == 0
    """

    env = os.environ.copy()
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "false"
    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, (stdout, stderr, status)
