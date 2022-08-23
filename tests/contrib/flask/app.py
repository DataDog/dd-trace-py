import os
import sys

from flask import Flask
from flask import request

from ddtrace import tracer
from tests.webclient import PingFilter


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)
cur_dir = os.path.dirname(os.path.realpath(__file__))
tmpl_path = os.path.join(cur_dir, "test_templates")
app = Flask(__name__, template_folder=tmpl_path)


@app.route("/")
def index():
    return "hello"


@app.route("/shutdown")
def shutdown():
    tracer.shutdown()
    sys.exit(0)


@app.route("/stream")
def hello():
    def resp():
        for i in range(10):
            yield str(i)

    return app.response_class(resp())


@app.route("/body")
def body():
    data = request.get_json()
    return data, 200
