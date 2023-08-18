import os

from flask import Flask

from ddtrace.appsec.iast import ddtrace_iast_flask_patch


os.environ["DD_IAST_ENABLED"] = "1"


def add_test(a, b):
    return a + b


app = Flask(__name__)

ddtrace_iast_flask_patch()

if __name__ == "__main__":
    app.run()
