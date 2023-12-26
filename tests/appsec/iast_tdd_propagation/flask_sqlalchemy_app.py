#!/usr/bin/env python3

""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""


from flask import Flask
from flask import request
from sqlalchemy import Column
from sqlalchemy import Integer
from sqlalchemy import String
from sqlalchemy import create_engine
from sqlalchemy.orm import declarative_base
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted


import ddtrace.auto  # noqa: F401  # isort: skip


# import pdb

# pdb.set_trace()

# engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
engine = create_engine("sqlite+pysqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
# engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False}, poolclass=StaticPool)
app = Flask(__name__)


Base = declarative_base()


class User(Base):
    __tablename__ = "user_account"
    # __table_args__ = {"sqlite_autoincrement": True}
    # id = Column(Integer, primary_key=True, nullable=True)
    # id = Column(Integer, primary_key=True, sqlite_on_conflict_not_null="FAIL")
    id = Column(Integer, primary_key=True)
    name = Column(String(30), nullable=True)


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
    response = ResultResponse(request.args.get("param"))
    session = sessionmaker(bind=engine)()
    my_user = User(name=request.args.get("param"))
    session.add(my_user)
    session.commit()

    response.result1 = request.args.get("param")
    response.result2 = ""
    return response.json()


if __name__ == "__main__":
    User.metadata.create_all(engine, checkfirst=False)
    app.run(debug=False, port=8000)
