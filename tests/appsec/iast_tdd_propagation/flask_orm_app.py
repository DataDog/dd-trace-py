#!/usr/bin/env python3

""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""


import importlib
import os
import sys

from flask import Flask
from flask import request

from ddtrace import tracer
from ddtrace.appsec._constants import IAST
from ddtrace.appsec.iast import ddtrace_iast_flask_patch
from ddtrace.internal import core
from tests.utils import override_env


with override_env({"DD_IAST_ENABLED": "True"}):
    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

import ddtrace.auto  # noqa: F401  # isort: skip

orm = os.getenv("FLASK_ORM", "sqlite")

orm_impl = importlib.import_module(f"{orm}_impl")


app = Flask(__name__)


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


@app.route("/shutdown")
def shutdown():
    tracer.shutdown()
    sys.exit(0)


@app.route("/")
def tainted_view():
    param = request.args.get("param", "param")

    report = core.get_items([IAST.CONTEXT_KEY], tracer.current_root_span())

    assert not (report and report[0])

    orm_impl.execute_query("select * from User where name = '" + param + "'")

    response = ResultResponse(param)
    report = core.get_items([IAST.CONTEXT_KEY], tracer.current_root_span())
    if report and report[0]:
        response.sources = report[0].sources[0].value
        response.vulnerabilities = list(report[0].vulnerabilities)[0].type

    return response.json()


@app.route("/untainted")
def untainted_view():
    param = request.args.get("param", "param")

    report = core.get_items([IAST.CONTEXT_KEY], tracer.current_root_span())

    assert not (report and report[0])

    orm_impl.execute_untainted_query("select * from User where name = '" + param + "'")

    response = ResultResponse(param)
    report = core.get_items([IAST.CONTEXT_KEY], tracer.current_root_span())
    if report and report[0]:
        response.sources = report[0].sources[0].value
        response.vulnerabilities = list(report[0].vulnerabilities)[0].type

    return response.json()


if __name__ == "__main__":
    ddtrace_iast_flask_patch()
    app.run(debug=False, port=8000)
