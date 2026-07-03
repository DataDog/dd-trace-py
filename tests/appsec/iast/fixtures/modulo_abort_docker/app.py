import os
import sys

from flask import Flask
from flask import request

from ddtrace.appsec.iast import ddtrace_iast_flask_patch
from ddtrace.trace import tracer


app = Flask(__name__)


@app.route("/format")
def format_value():
    value = request.args.get("value", "")
    # AIDEV-NOTE: Flask is only used to provide a normal request-tainted value.
    # The vulnerable behavior is in IAST modulo-format propagation.
    return "formatted=%s\n" % value


@app.route("/shutdown")
def shutdown():
    tracer.shutdown()
    sys.exit(0)


if __name__ == "__main__":
    ddtrace_iast_flask_patch()
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")), debug=False)
