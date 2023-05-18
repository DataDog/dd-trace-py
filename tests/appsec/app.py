from flask import Flask


import ddtrace.auto  # noqa: F401  # isort: skip


app = Flask(__name__)


@app.route("/")
def index():
    return "OK", 200
