""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""

import os
import subprocess  # nosec

from flask import Flask
from flask import Response
from flask import request


import ddtrace.auto  # noqa: F401  # isort: skip
from tests.appsec.iast_packages.packages.pkg_aiohttp import pkg_aiohttp
from tests.appsec.iast_packages.packages.pkg_aiosignal import pkg_aiosignal
from tests.appsec.iast_packages.packages.pkg_annotated_types import pkg_annotated_types
from tests.appsec.iast_packages.packages.pkg_asn1crypto import pkg_asn1crypto
from tests.appsec.iast_packages.packages.pkg_attrs import pkg_attrs
from tests.appsec.iast_packages.packages.pkg_beautifulsoup4 import pkg_beautifulsoup4
from tests.appsec.iast_packages.packages.pkg_cachetools import pkg_cachetools
from tests.appsec.iast_packages.packages.pkg_certifi import pkg_certifi
from tests.appsec.iast_packages.packages.pkg_cffi import pkg_cffi
from tests.appsec.iast_packages.packages.pkg_chartset_normalizer import pkg_chartset_normalizer
from tests.appsec.iast_packages.packages.pkg_click import pkg_click
from tests.appsec.iast_packages.packages.pkg_cryptography import pkg_cryptography
from tests.appsec.iast_packages.packages.pkg_decorator import pkg_decorator
from tests.appsec.iast_packages.packages.pkg_distlib import pkg_distlib
from tests.appsec.iast_packages.packages.pkg_docutils import pkg_docutils
from tests.appsec.iast_packages.packages.pkg_exceptiongroup import pkg_exceptiongroup
from tests.appsec.iast_packages.packages.pkg_filelock import pkg_filelock
from tests.appsec.iast_packages.packages.pkg_frozenlist import pkg_frozenlist
from tests.appsec.iast_packages.packages.pkg_fsspec import pkg_fsspec
from tests.appsec.iast_packages.packages.pkg_google_api_core import pkg_google_api_core
from tests.appsec.iast_packages.packages.pkg_google_api_python_client import pkg_google_api_python_client
from tests.appsec.iast_packages.packages.pkg_google_auth import pkg_google_auth
from tests.appsec.iast_packages.packages.pkg_idna import pkg_idna
from tests.appsec.iast_packages.packages.pkg_importlib_resources import pkg_importlib_resources
from tests.appsec.iast_packages.packages.pkg_iniconfig import pkg_iniconfig
from tests.appsec.iast_packages.packages.pkg_isodate import pkg_isodate
from tests.appsec.iast_packages.packages.pkg_itsdangerous import pkg_itsdangerous
from tests.appsec.iast_packages.packages.pkg_jinja2 import pkg_jinja2
from tests.appsec.iast_packages.packages.pkg_jmespath import pkg_jmespath
from tests.appsec.iast_packages.packages.pkg_jsonschema import pkg_jsonschema
from tests.appsec.iast_packages.packages.pkg_lxml import pkg_lxml
from tests.appsec.iast_packages.packages.pkg_markupsafe import pkg_markupsafe
from tests.appsec.iast_packages.packages.pkg_more_itertools import pkg_more_itertools
from tests.appsec.iast_packages.packages.pkg_moto import pkg_moto
from tests.appsec.iast_packages.packages.pkg_multidict import pkg_multidict
from tests.appsec.iast_packages.packages.pkg_numpy import pkg_numpy
from tests.appsec.iast_packages.packages.pkg_oauthlib import pkg_oauthlib
from tests.appsec.iast_packages.packages.pkg_openpyxl import pkg_openpyxl
from tests.appsec.iast_packages.packages.pkg_packaging import pkg_packaging
from tests.appsec.iast_packages.packages.pkg_pandas import pkg_pandas
from tests.appsec.iast_packages.packages.pkg_pillow import pkg_pillow
from tests.appsec.iast_packages.packages.pkg_platformdirs import pkg_platformdirs
from tests.appsec.iast_packages.packages.pkg_pluggy import pkg_pluggy
from tests.appsec.iast_packages.packages.pkg_psutil import pkg_psutil
from tests.appsec.iast_packages.packages.pkg_pyarrow import pkg_pyarrow
from tests.appsec.iast_packages.packages.pkg_pyasn1 import pkg_pyasn1
from tests.appsec.iast_packages.packages.pkg_pycparser import pkg_pycparser
from tests.appsec.iast_packages.packages.pkg_pydantic import pkg_pydantic
from tests.appsec.iast_packages.packages.pkg_pygments import pkg_pygments
from tests.appsec.iast_packages.packages.pkg_pyjwt import pkg_pyjwt
from tests.appsec.iast_packages.packages.pkg_pynacl import pkg_pynacl
from tests.appsec.iast_packages.packages.pkg_pyopenssl import pkg_pyopenssl
from tests.appsec.iast_packages.packages.pkg_pyparsing import pkg_pyparsing
from tests.appsec.iast_packages.packages.pkg_python_dateutil import pkg_python_dateutil
from tests.appsec.iast_packages.packages.pkg_pytz import pkg_pytz
from tests.appsec.iast_packages.packages.pkg_pyyaml import pkg_pyyaml
from tests.appsec.iast_packages.packages.pkg_requests import pkg_requests
from tests.appsec.iast_packages.packages.pkg_requests_toolbelt import pkg_requests_toolbelt
from tests.appsec.iast_packages.packages.pkg_rsa import pkg_rsa
from tests.appsec.iast_packages.packages.pkg_s3fs import pkg_s3fs
from tests.appsec.iast_packages.packages.pkg_s3transfer import pkg_s3transfer
from tests.appsec.iast_packages.packages.pkg_scipy import pkg_scipy
from tests.appsec.iast_packages.packages.pkg_setuptools import pkg_setuptools
from tests.appsec.iast_packages.packages.pkg_six import pkg_six
from tests.appsec.iast_packages.packages.pkg_soupsieve import pkg_soupsieve
from tests.appsec.iast_packages.packages.pkg_sqlalchemy import pkg_sqlalchemy
from tests.appsec.iast_packages.packages.pkg_tomli import pkg_tomli
from tests.appsec.iast_packages.packages.pkg_tomlkit import pkg_tomlkit
from tests.appsec.iast_packages.packages.pkg_urllib3 import pkg_urllib3
from tests.appsec.iast_packages.packages.pkg_virtualenv import pkg_virtualenv
from tests.appsec.iast_packages.packages.pkg_werkzeug import pkg_werkzeug
from tests.appsec.iast_packages.packages.pkg_wrapt import pkg_wrapt
from tests.appsec.iast_packages.packages.pkg_yarl import pkg_yarl
from tests.appsec.iast_packages.packages.pkg_zipp import pkg_zipp
import tests.appsec.integrations.module_with_import_errors as module_with_import_errors


app = Flask(__name__)
app.register_blueprint(pkg_aiohttp)
app.register_blueprint(pkg_aiosignal)
app.register_blueprint(pkg_annotated_types)
app.register_blueprint(pkg_asn1crypto)
app.register_blueprint(pkg_attrs)
app.register_blueprint(pkg_beautifulsoup4)
app.register_blueprint(pkg_cachetools)
app.register_blueprint(pkg_certifi)
app.register_blueprint(pkg_cffi)
app.register_blueprint(pkg_chartset_normalizer)
app.register_blueprint(pkg_click)
app.register_blueprint(pkg_cryptography)
app.register_blueprint(pkg_decorator)
app.register_blueprint(pkg_distlib)
app.register_blueprint(pkg_docutils)
app.register_blueprint(pkg_exceptiongroup)
app.register_blueprint(pkg_filelock)
app.register_blueprint(pkg_frozenlist)
app.register_blueprint(pkg_fsspec)
app.register_blueprint(pkg_google_auth)
app.register_blueprint(pkg_google_api_core)
app.register_blueprint(pkg_google_api_python_client)
app.register_blueprint(pkg_idna)
app.register_blueprint(pkg_importlib_resources)
app.register_blueprint(pkg_iniconfig)
app.register_blueprint(pkg_isodate)
app.register_blueprint(pkg_itsdangerous)
app.register_blueprint(pkg_jinja2)
app.register_blueprint(pkg_jmespath)
app.register_blueprint(pkg_jsonschema)
app.register_blueprint(pkg_lxml)
app.register_blueprint(pkg_markupsafe)
app.register_blueprint(pkg_more_itertools)
app.register_blueprint(pkg_moto)
app.register_blueprint(pkg_multidict)
app.register_blueprint(pkg_numpy)
app.register_blueprint(pkg_oauthlib)
app.register_blueprint(pkg_openpyxl)
app.register_blueprint(pkg_packaging)
app.register_blueprint(pkg_pandas)
app.register_blueprint(pkg_pillow)
app.register_blueprint(pkg_platformdirs)
app.register_blueprint(pkg_pluggy)
app.register_blueprint(pkg_psutil)
app.register_blueprint(pkg_pyarrow)
app.register_blueprint(pkg_pyasn1)
app.register_blueprint(pkg_pycparser)
app.register_blueprint(pkg_pydantic)
app.register_blueprint(pkg_pygments)
app.register_blueprint(pkg_pyjwt)
app.register_blueprint(pkg_pynacl)
app.register_blueprint(pkg_pyopenssl)
app.register_blueprint(pkg_pyparsing)
app.register_blueprint(pkg_python_dateutil)
app.register_blueprint(pkg_pytz)
app.register_blueprint(pkg_pyyaml)
app.register_blueprint(pkg_requests)
app.register_blueprint(pkg_requests_toolbelt)
app.register_blueprint(pkg_rsa)
app.register_blueprint(pkg_s3fs)
app.register_blueprint(pkg_s3transfer)
app.register_blueprint(pkg_scipy)
app.register_blueprint(pkg_setuptools)
app.register_blueprint(pkg_six)
app.register_blueprint(pkg_soupsieve)
app.register_blueprint(pkg_sqlalchemy)
app.register_blueprint(pkg_tomli)
app.register_blueprint(pkg_tomlkit)
app.register_blueprint(pkg_urllib3)
app.register_blueprint(pkg_virtualenv)
app.register_blueprint(pkg_werkzeug)
app.register_blueprint(pkg_wrapt)
app.register_blueprint(pkg_yarl)
app.register_blueprint(pkg_zipp)


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
    env_port = os.getenv("FLASK_RUN_PORT", 8000)
    app.run(debug=False, port=env_port)
