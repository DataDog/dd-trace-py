#!/usr/bin/env python3

""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""
from flask_taint_sinks_views import create_app

from ddtrace import auto  # noqa: F401


app = create_app()

if __name__ == "__main__":
    app.run(debug=False, port=8000)
