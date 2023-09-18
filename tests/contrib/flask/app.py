import os
import subprocess
import sys

from flask import Flask
from flask import request
from flask_login import LoginManager
from flask_login import UserMixin
from flask_login import current_user
from flask_login import login_user, logout_user
from werkzeug.security import generate_password_hash, check_password_hash

from ddtrace import tracer
from ddtrace.appsec.trace_utils import block_request_if_user_blocked
from ddtrace.contrib.trace_utils import set_user
from tests.webclient import PingFilter


tracer.configure(
    settings={
        "FILTERS": [PingFilter()],
    }
)
class User(UserMixin):
    def __init__(self, id, name, email, password, is_admin=False):
        self.id = id
        self.name = name
        self.email = email
        self.password = generate_password_hash(password)
        self.is_admin = is_admin

    def set_password(self, password):
        self.password = generate_password_hash(password)

    def check_password(self, password):
        return check_password_hash(self.password, password)

    def __repr__(self):
        return '<User {}>'.format(self.email)

users = [
    User(1, "john", "john@test.com", "passw0rd", False)
]


def get_user(email):
    for user in users:
        if user.email == email:
            return user
    return None
cur_dir = os.path.dirname(os.path.realpath(__file__))
tmpl_path = os.path.join(cur_dir, "test_templates")
app = Flask(__name__, template_folder=tmpl_path)
app.config['SECRET_KEY'] = '7110c8ae51a4b5af97be6534caef90e4bb9bdcb3380af008f90b23a5d1616bf319bc298105da20fe'
login_manager = LoginManager(app)


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


@login_manager.user_loader
def load_user(user_id):
    for user in users:
        if user.id == int(user_id):
            return user
    return None

def login_base(email, passwd):
    if current_user.is_authenticated:
        return "Already authenticated"

    user = get_user(email)
    if user is None:
        return "User not found"

    if user.check_password(passwd):
        login_user(user, remember=True)
        return "User %s logged in successfully, session: %s, dir(user): %s" % (TEST_USER, session["_id"], dir(user))
    else:
        return "Authentication failure"

@app.route('/logout')
def logout():
    logout_user()
    return "User logged out"
