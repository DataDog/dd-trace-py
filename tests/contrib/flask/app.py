import os
import subprocess
import sys

from flask import Flask
from flask import request

from ddtrace import tracer
from ddtrace.appsec._trace_utils import block_request_if_user_blocked
from ddtrace.contrib.trace_utils import set_user
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


@app.route("/identify")
def identify():
    set_user(
        tracer,
        user_id="usr.id",
        email="usr.email",
        name="usr.name",
        session_id="usr.session_id",
        role="usr.role",
        scope="usr.scope",
    )
    return "identify"


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


@app.route("/checkuser/<user_id>")
def checkuser(user_id):
    from ddtrace import tracer

    block_request_if_user_blocked(tracer, user_id)
    return "Ok", 200


@app.route("/executions/ossystem")
def run_ossystem():
    ret = os.system("dir -li /")
    return str(ret), 200


if sys.platform == "linux":

    @app.route("/executions/osspawn")
    def run_osspawn():
        args = ["/bin/ls", "-l", "/"]
        ret = os.spawnl(os.P_WAIT, args[0], *args)
        return str(ret), 200


@app.route("/executions/subcommunicateshell")
def run_subcommunicateshell():
    subp = subprocess.Popen(args=["dir", "-li", "/"], shell=True)
    subp.communicate()
    subp.wait()
    ret = subp.returncode
    return str(ret), 200


@app.route("/executions/subcommunicatenoshell")
def run_subcommunicatenoshell():
    subp = subprocess.Popen(args=["dir", "-li", "/"], shell=False)
    subp.communicate()
    subp.wait()
    ret = subp.returncode
    return str(ret), 200
