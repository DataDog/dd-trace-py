import subprocess
import threading
import time

import flask

import pytest

import requests

BACKEND_PORT = 8000


@pytest.fixture
def backend():

    PROFILES = []

    app = flask.Flask(__name__)

    @app.route("/", methods=["POST"])
    def post():
        PROFILES.append(dict(flask.request.form.lists()))
        return "got it"

    @app.route("/shutdown")
    def shutdown():
        flask.request.environ["werkzeug.server.shutdown"]()
        return "Bye!"

    @app.route("/")
    def get():
        return flask.jsonify(PROFILES)

    t = threading.Thread(target=app.run, kwargs={"host": "localhost", "port": BACKEND_PORT})
    # Set daemon just in case something fails
    t.daemon = True
    t.start()
    # wait for the server to be ready
    time.sleep(3)
    yield BACKEND_PORT
    requests.get("http://localhost:8000/shutdown")
    t.join()


@pytest.fixture(scope="module")
def toxiproxy():
    proxy_port = 8774
    proxy = subprocess.Popen(["toxiproxy-server", "-port", str(proxy_port)])
    # Wait for the proxy to start
    time.sleep(3)
    for cmd in (
        "toxiproxy-cli -host http://localhost:%d create profile_backend_latency -l localhost:%d -u localhost:%d"
        % (proxy_port, (BACKEND_PORT + 1), BACKEND_PORT),
        "toxiproxy-cli -host http://localhost:%d toxic add profile_backend_latency -t latency -a latency=1000"
        % proxy_port,
        "toxiproxy-cli -host http://localhost:%d create profile_backend_bandwidth -l localhost:%d -u localhost:%d"
        % (proxy_port, (BACKEND_PORT + 2), BACKEND_PORT),
        "toxiproxy-cli -host http://localhost:%d toxic add profile_backend_bandwidth -t bandwidth -a rate=2"
        % proxy_port,
        "toxiproxy-cli -host http://localhost:%d create profile_backend_slow_close -l localhost:%d -u localhost:%d"
        % (proxy_port, (BACKEND_PORT + 3), BACKEND_PORT),
        "toxiproxy-cli -host http://localhost:%d toxic add profile_backend_slow_close -t slow_close -a delay=10000"
        % proxy_port,
        "toxiproxy-cli -host http://localhost:%d create profile_backend_timeout -l localhost:%d -u localhost:%d"
        % (proxy_port, (BACKEND_PORT + 4), BACKEND_PORT),
        "toxiproxy-cli -host http://localhost:%d toxic add profile_backend_timeout -t timeout -a timeout=5000"
        % proxy_port,
        "toxiproxy-cli -host http://localhost:%d create profile_backend_limit_data -l localhost:%d -u localhost:%d"
        % (proxy_port, (BACKEND_PORT + 5), BACKEND_PORT),
        "toxiproxy-cli -host http://localhost:%d toxic add profile_backend_timeout -t limit_data -a bytes=20000"
        % proxy_port,
    ):
        subprocess.check_call(cmd, shell=True)
    yield proxy
    proxy.terminate()
    proxy.wait()
