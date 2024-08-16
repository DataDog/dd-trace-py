import os
import subprocess
import sys

import mock
import pytest

from ddtrace import Tracer


AGENT_VERSION = os.environ.get("AGENT_VERSION")


class BadEncoder:
    content_type = ""

    def __len__(self):
        return 0

    def put(self, trace):
        pass

    def encode(self):
        return b"bad_payload", 0

    def encode_traces(self, traces):
        return b"bad_payload"


def send_invalid_payload_and_get_logs(encoder_cls=BadEncoder):
    t = Tracer()
    for client in t._writer._clients:
        client.encoder = encoder_cls()
    with mock.patch("ddtrace.internal.writer.writer.log") as log:
        t.trace("asdf").finish()
        t.shutdown()
    return log


def parametrize_with_all_encodings(env=None, out="", err=""):
    if env is None:
        env = dict()
    return pytest.mark.subprocess(parametrize={"DD_TRACE_API_VERSION": ["v0.5", "v0.4"]}, env=env, out=out, err=err)


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
