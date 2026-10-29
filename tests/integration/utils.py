import os
import subprocess
import sys
from unittest import mock

import pytest


AGENT_VERSION = os.environ.get("AGENT_VERSION")


class BadEncoder:
    content_type = ""

    def __len__(self):
        return 0

    def put(self, trace):
        pass

    def encode(self):
        return [(b"bad_payload", 0)]

    def encode_traces(self, traces):
        return b"bad_payload"


def send_invalid_payload_and_get_logs(encoder_cls=BadEncoder):
    from ddtrace.trace import tracer as t

    for client in t._span_aggregator.writer._clients:
        client.encoder = encoder_cls()
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("asdf").finish()
        t.flush()
    return log


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


def skip_if_native_buffer(reason):
    """Skip when _DD_TRACE_NATIVE_BUFFER_ENABLED is opted in via the venv's env, not just the default.

    Use for tests whose mechanism (encoder/client swapping, dogstatsd wiring) has no NativeTraceBuffer
    equivalent yet, rather than duplicating assertions that would otherwise need per-writer branches.
    """
    return pytest.mark.skipif(
        os.environ.get("_DD_TRACE_NATIVE_BUFFER_ENABLED", "").lower() in ("1", "true", "yes"),
        reason=reason,
    )


def import_ddtrace_in_subprocess(env):
    p = subprocess.Popen(
        [sys.executable, "-c", "import ddtrace"],
        env=env or dict(),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    p.communicate()
    return p


def log_native_errors_to_a_file():
    """Send native error logs to a new file, and return its path.

    libdatadog logs through the native logger only, which ddtrace leaves off unless debug logging is
    on.
    """
    import tempfile

    from ddtrace.internal.native._native import logger

    path = os.path.join(tempfile.mkdtemp(), "native.log")
    logger.configure(output="file", path=path)
    logger.set_log_level("error")
    return path


def read_native_log(path):
    # The logger rotates files, so a message can sit in a rotated sibling of path.
    directory = os.path.dirname(path)
    contents = []
    for name in sorted(os.listdir(directory)):
        with open(os.path.join(directory, name)) as f:
            contents.append(f.read())
    return "".join(contents)
