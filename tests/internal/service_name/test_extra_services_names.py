import os
import sys

import pytest


@pytest.mark.skipif(sys.platform in ("win32", "cygwin"), reason="Fork not supported on Windows")
def test_config_extra_service_names_fork(run_python_code_in_subprocess):
    code = """
import ddtrace.auto
import ddtrace

import os
import sys

children = []
for i in range(10):
    pid = os.fork()
    if pid == 0:
        # Child process
        ddtrace.config._add_extra_service(f"extra_service_{i}")
        os._exit(0)
    else:
        # Parent process
        children.append(pid)

failed = []
for pid in children:
    _, status = os.waitpid(pid, 0)
    if status != 0:
        failed.append(pid)
if failed:
    sys.stderr.write(f"child processes failed: {failed}\\n")
    sys.exit(1)

extra_services = ddtrace.config._get_extra_services()
extra_services.discard("sqlite")  # coverage
expected = {f"extra_service_{i}" for i in range(10)}
if extra_services != expected:
    sys.stderr.write(
        f"missing extra services: {expected - extra_services}; "
        f"unexpected extra services: {extra_services - expected}\\n"
    )
    sys.exit(1)
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
assert extra_services == {"extra_service_1"}, extra_services
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


def test_config_extra_service_names_customer_changes(run_python_code_in_subprocess):
    code = """
import ddtrace.auto
import ddtrace
import re
import os
import sys
import time

with ddtrace.tracer.trace("test") as parent:
    parent.service = "parent_service"
    with ddtrace.tracer.trace("child") as child:
        child.service = "child_service"
extra_services = ddtrace.config._get_extra_services()
# collecting extra services in all spans, including the parent and child
assert "parent_service" in extra_services
assert "child_service" in extra_services
    """

    env = os.environ.copy()
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "true"
    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, (stdout, stderr, status)
