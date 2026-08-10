"""Minimal Flask app with DispatcherMiddleware for snapshot tests.

Verifies that ``flask.resource.full`` appears on spans when a sub-app
is mounted under a prefix.
"""

import sys

from flask import Flask
from werkzeug.middleware.dispatcher import DispatcherMiddleware

from ddtrace.trace import tracer
from tests.webclient import PingFilter


tracer.configure(trace_processors=[PingFilter()])

root = Flask("root")
api = Flask("api")


@root.route("/")
def index():
    return "root"


@root.route("/shutdown")
def shutdown():
    tracer.shutdown()
    sys.exit(0)


@api.route("/users")
def users():
    return "users"


root.wsgi_app = DispatcherMiddleware(root.wsgi_app, {"/api": api})
app = root
