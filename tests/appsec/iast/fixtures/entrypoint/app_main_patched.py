# Normal flask app. With IAST propagation
from flask import Flask

from ddtrace.appsec._iast import ddtrace_iast_flask_patch


def add_test(a, b):
    return a + b


app = Flask(__name__)

ddtrace_iast_flask_patch()

if __name__ == "__main__":
    app.run()
