import os
import subprocess
import sys

import pytest


AGENT_VERSION = os.environ.get("AGENT_VERSION")


def parametrize_with_all_encodings(env=None, out="", err="", check_logs=True):
    if env is None:
        env = dict()
    return pytest.mark.subprocess(
        parametrize={"DD_TRACE_API_VERSION": ["v0.5", "v0.4"]}, env=env, out=out, err=err, check_logs=check_logs
    )


def mark_snapshot(f):
    f = pytest.mark.snapshot()(f)
    return pytest.mark.skipif(
        AGENT_VERSION != "testagent", reason="snapshot tests are only compatible with the testagent"
    )(f)


def skip_if_testagent(f):
    return pytest.mark.skipif(
        AGENT_VERSION == "testagent", reason="FIXME: Test agent doesn't support this for some reason."
    )(f)


def import_ddtrace_in_subprocess(env):
    p = subprocess.Popen(
        [sys.executable, "-c", "import ddtrace"],
        env=env or dict(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.wait()
    return p
