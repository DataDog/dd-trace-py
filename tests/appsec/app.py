""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""
from flask import Flask


import ddtrace.auto  # noqa: F401  # isort: skip


app = Flask(__name__)


@app.route("/")
def index():
    return "OK_index", 200


@app.route("/test-body-hang", methods=["POST"])
def apsec_body_hang():
    return "OK_test-body-hang", 200


if __name__ == "__main__":
    app.run(debug=True, port=8000)
