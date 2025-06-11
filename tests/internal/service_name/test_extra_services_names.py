import os

import pytest


@pytest.mark.parametrize("child_services", [1, 20])
def test_config_extra_service_names(child_services, run_python_code_in_subprocess):
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
        print(ddtrace.config._extra_services_queue)
        for c in range({child_services}):
            ddtrace.config._add_extra_service(f"extra_service_{{i}}_{{c}}")
        sys.exit(0)
    else:
        # Parent process
        children.append(pid)

for pid in children:
    os.waitpid(pid, 0)

extra_services = ddtrace.config._get_extra_services()
extra_services.discard("sqlite")  # coverage
assert len(extra_services) == min(10 * {child_services}, 64), extra_services
assert all(re.match(r"extra_service_\\d+_\\d+", service) for service in extra_services)
"""

    env = os.environ.copy()
    env["DD_REMOTE_CONFIGURATION_ENABLED"] = "true"
    stdout, stderr, status, _ = run_python_code_in_subprocess(code, env=env)
    assert status == 0, (stdout, stderr, status)
