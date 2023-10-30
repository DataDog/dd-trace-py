""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""
from flask import Flask
from flask import request


import ddtrace.auto  # noqa: F401  # isort: skip


app = Flask(__name__)


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
def apsec_body_hang():
    return "OK_test-body-hang", 200


if __name__ == "__main__":
    app.run(debug=True, port=8000)
