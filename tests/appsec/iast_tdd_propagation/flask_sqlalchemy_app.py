#!/usr/bin/env python3

""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""

import sys

from flask import Flask
from flask import request
from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import create_engine
from sqlalchemy import text
from sqlalchemy.orm import declarative_base
from sqlalchemy.pool import StaticPool

from ddtrace import tracer
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast import ddtrace_iast_flask_patch
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.internal import core


import ddtrace.auto  # noqa: F401  # isort: skip


engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
app = Flask(__name__)

Base = declarative_base()


class User(Base):
    __tablename__ = "user_account"
    id = Column(Integer, primary_key=True)
    name = Column(String(30), nullable=True)


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
def pkg_requests_view():
    param = request.args.get("param", "param")

    with engine.connect() as connection:
        result = connection.execute(text("select * from user_account where name = '" + param + "'"))  # noqa: F841

    response = ResultResponse(param)
    report = core.get_items([IAST.CONTEXT_KEY], tracer.current_root_span())
    if report and report[0]:
        response.sources = report[0].sources[0].value
        response.vulnerabilities = list(report[0].vulnerabilities)[0].type

    return response.json()


if __name__ == "__main__":
    ddtrace_iast_flask_patch()
    User.metadata.create_all(engine)
    app.run(debug=False, port=8000)
