import os
import subprocess

import bm
import requests
import tenacity


SERVER_URL = "http://0.0.0.0:8000"


@tenacity.retry(
    wait=tenacity.wait_fixed(0.2),
    stop=tenacity.stop_after_attempt(1570),
)
def _get_response(path="/"):
    r = requests.get(SERVER_URL + path)
    r.raise_for_status()


def server(scenario):
    env = {
        "DD_TRACE_DEBUG": str(scenario.tracer_debug),
        "DD_ENV": "prod",
        "_DD_IAST_PATCH_MODULES": "benchmarks.,tests.appsec.",
        "DD_VERSION": "1.0",
        "DD_APPSEC_ENABLED": str(scenario.appsec_enabled),
        "DD_IAST_ENABLED": str(scenario.iast_enabled),
    }
    # copy over current environ
    env.update(os.environ)
    if scenario.tracer_enabled:
        cmd = ["python", "-m", "ddtrace.commands.ddtrace_run", "python", "manage.py", "runserver", "--noreload"]
    else:
        cmd = ["python", "manage.py", "runserver", "--noreload"]
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, start_new_session=True, text=True, env=env
    )
    # make sure process has been started
    try:
        # print("Starting server....")
        _get_response()
        # print("Shutdown server....")
        _get_response(path="/shutdown")
    except Exception as e:
        proc.terminate()
        proc.wait()
        raise AssertionError(
            "Server failed to start, see stdout and stderr logs\n."
            "Exception %s\n."
            "\n=== Captured STDOUT ===\n%s=== End of captured STDOUT ==="
            "\n=== Captured STDERR ===\n%s=== End of captured STDERR ===" % (e, proc.stdout.read(), proc.stderr.read())
        )

    proc.terminate()
    proc.wait()


class IASTDjangoStartup(bm.Scenario):
    tracer_enabled: bool
    tracer_debug: bool
    appsec_enabled: bool
    iast_enabled: bool

    def run(self):
        def _(loops):
            for _ in range(loops):
                server(self)

        yield _
