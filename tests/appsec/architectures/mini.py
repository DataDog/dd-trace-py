import ddtrace.auto  # noqa: F401


"""do not move this import"""

import os  # noqa: E402
import signal  # noqa: E402
import sys  # noqa: E402

from flask import Flask  # noqa: E402
from flask import request  # noqa: E402
import requests  # noqa: E402 F401

from ddtrace.settings.asm import config as asm_config  # noqa: E402
from ddtrace.version import get_version  # noqa: E402


app = Flask(__name__)
_TELEMETRY_DEPENDENCIES = []

# intercept telemetry events
from ddtrace.internal.telemetry.writer import TelemetryWriter  # noqa: E402


_flush_events = TelemetryWriter._flush_events_queue


def _flush_events_wrapper(self):
    global _TELEMETRY_DEPENDENCIES
    res = _flush_events(self)
    if res:
        dependencies = [v.get("payload", {}).get("dependencies", {}) for v in res]
        dependencies = [d for d in dependencies if d]
        for lst in dependencies:
            _TELEMETRY_DEPENDENCIES.extend(lst)
        print(f"flushed events {dependencies}", flush=True)
    return res


TelemetryWriter._flush_events_queue = _flush_events_wrapper


@app.route("/")
def hello_world():
    res = []
    for m in sys.modules:
        if m.startswith("ddtrace.appsec"):
            res.append(m)
    with open(__file__) as f:
        # open a file to trigger exploit prevention instrumentation
        file_length = len(f.read())
    return {
        "appsec": list(sorted(res)),
        "asm_config": {
            k: getattr(asm_config, k) for k in dir(asm_config) if isinstance(getattr(asm_config, k), (int, bool, float))
        },
        "aws": "AWS_LAMBDA_FUNCTION_NAME" in os.environ,
        "version": get_version(),
        "env": dict(os.environ),
        "file_length": file_length,
    }


@app.route("/import")
def import_modules():
    res = []
    loaded = {}
    module_name = request.args.get("module")
    if module_name:
        __import__(module_name)

    from ddtrace.internal.telemetry.data import update_imported_dependencies  # noqa: E402

    newly_loaded = list(sys.modules.keys())
    res = update_imported_dependencies(loaded, newly_loaded)

    return {
        "dependencies": res,
    }


@app.route("/telemetrydependencies")
def telemetry_dependencies():
    return {
        "dependencies": _TELEMETRY_DEPENDENCIES,
    }


@app.route("/shutdown")
def shutdown():
    os.kill(os.getpid(), signal.SIGTERM)
    return "Shutting down"


if __name__ == "__main__":
    app.run(debug=True, port=8475)
