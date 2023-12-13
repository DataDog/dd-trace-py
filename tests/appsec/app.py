""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""

import subprocess  # nosec

from flask import Flask
from flask import Response
from flask import request


import ddtrace.auto  # noqa: F401  # isort: skip
from tests.appsec.iast_packages.packages.pkg_requests import pkg_requests


app = Flask(__name__)
app.register_blueprint(pkg_requests)


@app.route("/")
def index():
    return "OK_index", 200


@app.route("/submit/file", methods=["POST"])
def submit_file():
    user_file = request.stream.read()
    if not user_file:
        raise Exception("user_file is missing")
    return "OK_file"


@app.route("/test-body-hang", methods=["POST"])
def appsec_body_hang():
    return "OK_test-body-hang", 200


@app.route("/iast-cmdi-vulnerability", methods=["GET"])
def iast_cmdi_vulnerability():
    filename = request.args.get("filename")
    subp = subprocess.Popen(args=["ls", "-la", filename])
    subp.communicate()
    subp.wait()
    resp = Response("OK")
    resp.set_cookie("insecure", "cookie", secure=True, httponly=True, samesite="None")
    return resp


if __name__ == "__main__":
    app.run(debug=False, port=8000)
