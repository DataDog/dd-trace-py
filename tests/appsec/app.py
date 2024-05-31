""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""

import subprocess  # nosec

from flask import Flask
from flask import Response
from flask import request


import ddtrace.auto  # noqa: F401  # isort: skip
from tests.appsec.iast_packages.packages.pkg_beautifulsoup4 import pkg_beautifulsoup4
from tests.appsec.iast_packages.packages.pkg_chartset_normalizer import pkg_chartset_normalizer
from tests.appsec.iast_packages.packages.pkg_google_api_core import pkg_google_api_core
from tests.appsec.iast_packages.packages.pkg_idna import pkg_idna
from tests.appsec.iast_packages.packages.pkg_numpy import pkg_numpy
from tests.appsec.iast_packages.packages.pkg_python_dateutil import pkg_python_dateutil
from tests.appsec.iast_packages.packages.pkg_pyyaml import pkg_pyyaml
from tests.appsec.iast_packages.packages.pkg_requests import pkg_requests
from tests.appsec.iast_packages.packages.pkg_urllib3 import pkg_urllib3
import tests.appsec.integrations.module_with_import_errors as module_with_import_errors


app = Flask(__name__)
app.register_blueprint(pkg_beautifulsoup4)
app.register_blueprint(pkg_chartset_normalizer)
app.register_blueprint(pkg_google_api_core)
app.register_blueprint(pkg_idna)
app.register_blueprint(pkg_numpy)
app.register_blueprint(pkg_python_dateutil)
app.register_blueprint(pkg_pyyaml)
app.register_blueprint(pkg_requests)
app.register_blueprint(pkg_urllib3)


@app.route("/")
def index():
    return "OK_index", 200


@app.route("/submit/file", methods=["POST"])
def submit_file():
    user_file = request.stream.read()
    if not user_file:
        raise Exception("user_file is missing")
    return "OK_file"


@app.route("/test-body-hang", methods=["POST"])
def appsec_body_hang():
    return "OK_test-body-hang", 200


@app.route("/iast-cmdi-vulnerability", methods=["GET"])
def iast_cmdi_vulnerability():
    filename = request.args.get("filename")
    subp = subprocess.Popen(args=["ls", "-la", filename])
    subp.communicate()
    subp.wait()
    resp = Response("OK")
    resp.set_cookie("insecure", "cookie", secure=True, httponly=True, samesite="None")
    return resp


@app.route("/iast-ast-patching-import-error", methods=["GET"])
def iast_ast_patching_import_error():
    return Response(str(module_with_import_errors.verbal_kint_is_keyser_soze))


if __name__ == "__main__":
    app.run(debug=False, port=8000)
