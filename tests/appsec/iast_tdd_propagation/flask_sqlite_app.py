#!/usr/bin/env python3

""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""


from flask import Flask
from flask import request
from ddtrace.appsec import _asm_request_context

from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted


import sqlite3
import ddtrace.auto  # noqa: F401  # isort: skip

conn = sqlite3.connect(":memory:", check_same_thread=False)
cursor = conn.cursor()

app = Flask(__name__)


class ResultResponse:
    param = ""
    result1 = ""
    result2 = ""

    def __init__(self, param):
        self.param = param

    def json(self):
        return {
            "param": self.param,
            "result1": self.result1,
            "result2": self.result2,
            "params_are_tainted": is_pyobject_tainted(self.result1),
        }


@app.route("/")
def pkg_requests_view():
    param = request.args.get("param", "param")
    response = ResultResponse(param)

    result = cursor.execute("select * from sqlite_master where name = '" + param + "'")
    results = cursor.fetchall()
    conn.commit()

    response.result1 = param
    response.result2 = ""

    return response.json()


if __name__ == "__main__":
    app.run(debug=False, port=8000)
