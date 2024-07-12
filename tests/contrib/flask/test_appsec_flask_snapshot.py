import os
import signal
import subprocess
import sys
import time
from typing import Callable  # noqa:F401
from typing import Dict  # noqa:F401
from typing import Generator  # noqa:F401
from typing import List  # noqa:F401

import pytest

from ddtrace.appsec._constants import APPSEC
from ddtrace.contrib.flask.patch import flask_version
from ddtrace.internal.constants import BLOCKED_RESPONSE_HTML
from ddtrace.internal.constants import BLOCKED_RESPONSE_JSON
from ddtrace.internal.utils.retry import RetryError
import tests.appsec.rules as rules
from tests.contrib.flask.test_flask_appsec import _ALLOWED_USER
from tests.contrib.flask.test_flask_appsec import _BLOCKED_USER
from tests.webclient import Client


DEFAULT_HEADERS = {
    "User-Agent": "python-httpx/x.xx.x",
}

APPSEC_JSON_TAG = f"meta.{APPSEC.JSON}"


@pytest.fixture
def flask_port():
    # type: () -> str
    return "8000"


@pytest.fixture
def flask_wsgi_application():
    # type: () -> str
    return "tests.contrib.flask.app:app"


@pytest.fixture
def flask_command(flask_wsgi_application, flask_port):
    # type: (str, str) -> List[str]
    cmd = "ddtrace-run flask run -h 0.0.0.0 -p %s" % (flask_port,)
    return cmd.split()


def flask_appsec_good_rules_env(flask_wsgi_application):
    # type: (str) -> Dict[str, str]
    env = os.environ.copy()
    env.update(
        {
            # Avoid noisy database spans being output on app startup/teardown.
            "DD_TRACE_SQLITE3_ENABLED": "0",
            "FLASK_APP": flask_wsgi_application,
            "DD_APPSEC_ENABLED": "true",
            "DD_APPSEC_RULES": rules.RULES_GOOD_PATH,
        }
    )
    return env


@pytest.fixture
def flask_client(flask_command, flask_port, flask_wsgi_application, flask_env_arg):
    # type: (List[str], Dict[str, str], str, Callable) -> Generator[Client, None, None]
    # Copy the env to get the correct PYTHONPATH and such
    # from the virtualenv.
    # webservers might exec or fork into another process, so we need to os.setsid() to create a process group
    # (all of which will listen to signals sent to the parent) so that we can kill the whole application.
    proc = subprocess.Popen(
        flask_command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env=flask_env_arg(flask_wsgi_application),
        preexec_fn=os.setsid,
        cwd=str(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__))))),
    )
    try:
        client = Client("http://0.0.0.0:%s" % flask_port)
        # Wait for the server to start up
        try:
            client.wait()
        except RetryError:
            # process failed
            stdout = proc.stdout.read()
            stderr = proc.stderr.read()
            raise TimeoutError(
                "Server failed to start\n======STDOUT=====%s\n\n======STDERR=====%s\n" % (stdout, stderr)
            )
        yield client
        try:
            client.get_ignored("/shutdown")
        except Exception:
            pass
        # At this point the traces have been sent to the test agent
        # but the test agent hasn't necessarily finished processing
        # the traces (race condition) so wait just a bit for that
        # processing to complete.
        time.sleep(0.2)
    finally:
        os.killpg(proc.pid, signal.SIGKILL)
        proc.wait()
    # DEV uncomment those lines if you need more info locally
    # stdout = proc.stdout.read()
    # print(stdout)
    # stderr = proc.stderr.read()
    # print(stderr.decode("UTF-8"))


@pytest.mark.snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.flask.version",
        "meta.http.request.headers.accept",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.host",
        "meta.http.request.headers.user-agent",
        "meta_struct",
        "http.response.headers.content-length",
        "http.response.headers.content-type",
        "meta.http.useragent",
        "meta.error.stack",
        "metrics._dd.appsec.event_rules.loaded",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "meta.span.kind",
    ],
    variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)},
)
@pytest.mark.parametrize("flask_env_arg", (flask_appsec_good_rules_env,))
def test_flask_ipblock_match_403(flask_client):
    resp = flask_client.get("/", headers={"X-Real-Ip": rules._IP.BLOCKED, "ACCEPT": "text/html"})
    assert resp.status_code == 403
    if hasattr(resp, "text"):
        assert resp.text == BLOCKED_RESPONSE_HTML
    else:
        assert resp.data == BLOCKED_RESPONSE_HTML.encode("utf-8")


@pytest.mark.snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.flask.version",
        "meta.http.request.headers.accept",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.host",
        "meta.http.request.headers.user-agent",
        "meta_struct",
        "http.response.headers.content-length",
        "http.response.headers.content-type",
        "meta.http.useragent",
        "meta.error.stack",
        "metrics._dd.appsec.event_rules.loaded",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
        "meta.span.kind",
    ],
    variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)},
)
@pytest.mark.parametrize("flask_env_arg", (flask_appsec_good_rules_env,))
def test_flask_ipblock_match_403_json(flask_client):
    resp = flask_client.get("/", headers={"X-Real-Ip": rules._IP.BLOCKED})
    assert resp.status_code == 403
    if hasattr(resp, "text"):
        assert resp.text == BLOCKED_RESPONSE_JSON
    else:
        assert resp.data == BLOCKED_RESPONSE_JSON.encode("utf-8")


@pytest.mark.snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.flask.version",
        "meta.http.request.headers.accept",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.host",
        "meta.http.request.headers.user-agent",
        "meta_struct",
        "http.response.headers.content-length",
        "http.response.headers.content-type",
        "meta.http.useragent",
        "meta.error.stack",
        "metrics._dd.appsec.event_rules.loaded",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ],
    variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)},
)
@pytest.mark.parametrize("flask_env_arg", (flask_appsec_good_rules_env,))
def test_flask_userblock_match_403_json(flask_client):
    resp = flask_client.get("/checkuser/%s" % _BLOCKED_USER)
    assert resp.status_code == 403
    if hasattr(resp, "text"):
        assert resp.text == BLOCKED_RESPONSE_JSON
    else:
        assert resp.data == BLOCKED_RESPONSE_JSON.encode("utf-8")


@pytest.mark.snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.flask.version",
        "meta.http.request.headers.accept",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.host",
        "meta.http.request.headers.user-agent",
        "meta_struct",
        "http.response.headers.content-length",
        "http.response.headers.content-type",
        "meta.http.useragent",
        "meta.error.stack",
        "metrics._dd.appsec.event_rules.loaded",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ],
    variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)},
)
@pytest.mark.parametrize("flask_env_arg", (flask_appsec_good_rules_env,))
def test_flask_userblock_match_200_json(flask_client):
    resp = flask_client.get("/checkuser/%s" % _ALLOWED_USER)
    assert resp.status_code == 200


@pytest.mark.snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.flask.version",
        "meta.http.request.headers.accept",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.host",
        "meta.http.request.headers.user-agent",
        "meta_struct",
        "http.response.headers.content-length",
        "http.response.headers.content-type",
        "meta.http.useragent",
        "meta.error.stack",
        "metrics._dd.appsec.event_rules.loaded",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ],
    variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)},
)
@pytest.mark.parametrize("flask_env_arg", (flask_appsec_good_rules_env,))
def test_flask_processexec_ossystem(flask_client):
    resp = flask_client.get("/executions/ossystem")
    assert resp.status_code == 200
    assert resp.text == "0"


@pytest.mark.snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.flask.version",
        "meta.http.request.headers.accept",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.host",
        "meta.http.request.headers.user-agent",
        "meta_struct",
        "http.response.headers.content-length",
        "http.response.headers.content-type",
        "meta.http.useragent",
        "meta.error.stack",
        "metrics._dd.appsec.event_rules.loaded",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ],
    variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)},
)
@pytest.mark.parametrize("flask_env_arg", (flask_appsec_good_rules_env,))
@pytest.mark.skipif(sys.platform != "linux", reason="Only for Linux")
def test_flask_processexec_osspawn(flask_client):
    resp = flask_client.get("/executions/osspawn")
    assert resp.status_code == 200
    assert resp.text == "0"


@pytest.mark.snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.flask.version",
        "meta.http.request.headers.accept",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.host",
        "meta.http.request.headers.user-agent",
        "meta_struct",
        "http.response.headers.content-length",
        "http.response.headers.content-type",
        "meta.http.useragent",
        "meta.error.stack",
        "metrics._dd.appsec.event_rules.loaded",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ],
    variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)},
)
@pytest.mark.parametrize("flask_env_arg", (flask_appsec_good_rules_env,))
def test_flask_processexec_subprocesscommunicateshell(flask_client):
    resp = flask_client.get("/executions/subcommunicateshell")
    assert resp.status_code == 200
    assert resp.text == "0"


@pytest.mark.snapshot(
    ignores=[
        "error",
        "type",
        "meta._dd.appsec.waf.duration",
        "meta._dd.appsec.waf.duration_ext",
        APPSEC_JSON_TAG,
        "meta.flask.version",
        "meta.http.request.headers.accept",
        "meta.http.request.headers.accept-encoding",
        "meta.http.request.headers.host",
        "meta.http.request.headers.user-agent",
        "meta_struct",
        "http.response.headers.content-length",
        "http.response.headers.content-type",
        "meta.http.useragent",
        "meta.error.stack",
        "metrics._dd.appsec.event_rules.loaded",
        "metrics._dd.appsec.waf.duration",
        "metrics._dd.appsec.waf.duration_ext",
    ],
    variants={"220": flask_version >= (2, 2, 0), "": flask_version < (2, 2, 0)},
)
@pytest.mark.parametrize("flask_env_arg", (flask_appsec_good_rules_env,))
def test_flask_processexec_subprocesscommunicatenoshell(flask_client):
    resp = flask_client.get("/executions/subcommunicatenoshell")
    assert resp.status_code == 200
    assert resp.text == "0"
