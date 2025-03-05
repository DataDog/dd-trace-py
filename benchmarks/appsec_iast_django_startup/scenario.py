import os
import subprocess

import bm
import requests
import tenacity


SERVER_URL = "http://0.0.0.0:8000/"


@tenacity.retry(
    wait=tenacity.wait_fixed(0.1),
    stop=tenacity.stop_after_attempt(80),
)
def _get_response(path=""):
    r = requests.get(SERVER_URL + path)
    r.raise_for_status()


def server(scenario):
    env = {
        "DD_APPSEC_ENABLED": str(scenario.appsec_enabled),
        "DD_IAST_ENABLED": str(scenario.iast_enabled),
    }
    # copy over current environ
    env.update(os.environ)
    if scenario.tracer_enabled:
        cmd = ["ddtrace-run", "python", "manage.py", "runserver"]
    else:
        cmd = ["python", "manage.py", "runserver"]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, text=True, env=env
    )
    # make sure process has been started
    assert proc.poll() is None
    try:
        _get_response()
    except Exception as e:
        raise AssertionError(
            "Server failed to start, see stdout and stderr logs\n."
            "Exception %s\n."
            "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
            "\n=== Captured STDERR ===\n%s=== End of captured STDERR ===" % (e, proc.stdout.read(), proc.stderr.read())
        )
    finally:
        proc.terminate()
        proc.wait()


class IASTDjangoStartup(bm.Scenario):
    tracer_enabled: bool
    appsec_enabled: bool
    iast_enabled: bool

    def run(self):
        def _(loops):
            for _ in range(loops):
                server(self)

        yield _
