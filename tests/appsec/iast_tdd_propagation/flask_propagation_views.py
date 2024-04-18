import sys

from flask import Flask
from flask import request

from ddtrace import tracer
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted


class ResultResponse:
    param = ""
    sources = ""
    vulnerabilities = ""

    def __init__(self, param):
        self.param = param

    def json(self):
        return {
            "param": self.param,
            "sources": self.sources,
            "vulnerabilities": self.vulnerabilities,
            "params_are_tainted": is_pyobject_tainted(self.param),
        }


def create_app():
    app = Flask(__name__)

    @app.route("/shutdown")
    def shutdown():
        tracer.shutdown()
        sys.exit(0)

    @app.route("/")
    def index():
        return "OK"

    @app.route("/check-headers")
    def check_headers():
        headers = list(request.headers.items())

        response = ResultResponse(headers)

        return response.json()

    return app
