#!/usr/bin/env python3

import os
import subprocess
import time

import pytest


@pytest.mark.parametrize("iast_enabled", ["true", "false"])
@pytest.mark.parametrize("iast_request_sampling", ["100.0", "0.0"])
def test_flask_pytest_iast(iast_enabled, iast_request_sampling):
    from tests.utils import _build_env

    env = _build_env()
    env.update(
        {
            # Avoid noisy database spans being output on app startup/teardown.
            "DD_TRACE_SQLITE3_ENABLED": "0",
            "DD_TRACE_SQLITE_ENABLED": "0",
            "DD_IAST_ENABLED": iast_enabled,
            "DD_TRACE_DEBUG": "true",
            "DD_IAST_REQUEST_SAMPLING": iast_request_sampling,
            # "DD_API_KEY": "invalidapikey",
            # "DD_CIVISIBILITY_AGENTLESS_ENABLED": "1",
        }
    )
    proc = subprocess.Popen(
        "pytest --ddtrace --ddtrace-patch-all --no-cov tests/contrib/flask/test_flask_pytest_iast.py".split(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=env,
        preexec_fn=os.setsid,
        cwd=str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
    )
    try:
        time.sleep(0.2)
    finally:
        proc.wait()
    # DEV uncomment this line if you need more info locally
    # stdout = proc.stdout.read()

    stderr = proc.stderr.read()
    split_stderr = stderr.decode("utf-8").split("\n")

    found = False
    for line in split_stderr:
        if "WEAK_HASH" in line:
            assert line.startswith("finishing span name='pytest.test'")
            found = True
            break

    if iast_enabled == "true" and iast_request_sampling == "100":
        assert found
    else:
        assert not found
