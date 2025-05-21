""" This Flask application is imported on tests.appsec.appsec_utils.gunicorn_server
"""
import copy
import os
import re
import shlex
import subprocess  # nosec

from flask import Flask
from flask import Response
from flask import request
from wrapt import FunctionWrapper


import ddtrace.auto  # noqa: F401  # isort: skip
from ddtrace import tracer
from ddtrace.appsec._iast import ddtrace_iast_flask_patch
from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted
from ddtrace.internal.utils.formats import asbool
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
from tests.appsec.iast_packages.packages.pkg_python_multipart import pkg_python_multipart
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
import tests.appsec.integrations.flask_tests.module_with_import_errors as module_with_import_errors


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
app.register_blueprint(pkg_python_multipart)
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
    return resp


@app.route("/iast-cmdi-vulnerability-secure", methods=["GET"])
def view_cmdi_secure():
    filename = request.args.get("filename")
    subp = subprocess.Popen(args=["ls", "-la", shlex.quote(filename)])
    subp.wait()
    return Response("OK")


@app.route("/iast-header-injection-vulnerability", methods=["GET"])
def iast_header_injection_vulnerability():
    header = request.args.get("header")
    resp = Response("OK")
    resp.headers["Header-Injection"] = header
    return resp


@app.route("/iast-code-injection", methods=["GET"])
def iast_code_injection_vulnerability():
    filename = request.args.get("filename")
    a = ""  # noqa: F841
    c = eval("a + '" + filename + "'")
    resp = Response(f"OK:{tracer._span_aggregator.writer._api_version}:{c}")
    return resp


@app.route("/shutdown", methods=["GET"])
def shutdown_view():
    tracer._span_aggregator.writer.flush_queue()
    return "OK"


@app.route("/iast-stacktrace-leak-vulnerability", methods=["GET"])
def iast_stacktrace_vulnerability():
    raise ValueError("Check my stacktrace!")
    return "OK"


@app.route("/iast-weak-hash-vulnerability", methods=["GET"])
def iast_weak_hash_vulnerability():
    import _md5

    m = _md5.md5()
    m.update(b"Nobody inspects")
    m.update(b" the spammish repetition")
    m.digest()
    from ddtrace.internal import telemetry

    list_metrics_logs = list(telemetry.telemetry_writer._logs)
    return str(list_metrics_logs)


@app.route("/iast-ast-patching-import-error", methods=["GET"])
def iast_ast_patching_import_error():
    return Response(str(module_with_import_errors.verbal_kint_is_keyser_soze))


@app.route("/iast-ast-patching-io-bytesio-untainted", methods=["GET"])
def iast_ast_patching_io_bytes_io_untainted():
    filename = "filename"
    style = request.args.get("style")
    bytes_filename = filename.encode()
    if style == "_io_module":
        import _io

        changed = _io.BytesIO(bytes_filename)
    elif style == "io_module":
        import io

        changed = io.BytesIO(bytes_filename)
    elif style == "io_function":
        from io import BytesIO

        changed = BytesIO(bytes_filename)
    else:
        from _io import BytesIO

        changed = BytesIO(bytes_filename)
    resp = Response("Fail")
    if not is_pyobject_tainted(changed):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-io-stringio-untainted", methods=["GET"])
def iast_ast_patching_io_string_io_untainted():
    filename = "filename"
    style = request.args.get("style")
    if style == "_io_module":
        import _io

        changed = _io.StringIO(filename)
    elif style == "io_module":
        import io

        changed = io.StringIO(filename)
    elif style == "io_function":
        from io import StringIO

        changed = StringIO(filename)
    else:
        from _io import StringIO

        changed = StringIO(filename)
    resp = Response("Fail")
    if not is_pyobject_tainted(changed):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-io-bytesio-read-untainted", methods=["GET"])
def iast_ast_patching_io_bytes_io_read_untainted():
    filename = "filename"
    style = request.args.get("style")
    bytes_filename = filename.encode()
    if style == "_io_module":
        import _io

        changed = _io.BytesIO(bytes_filename)
    elif style == "io_module":
        import io

        changed = io.BytesIO(bytes_filename)
    elif style == "io_function":
        from io import BytesIO

        changed = BytesIO(bytes_filename)
    else:
        from _io import BytesIO

        changed = BytesIO(bytes_filename)
    resp = Response("Fail")
    if not is_pyobject_tainted(changed.read(4)):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-io-stringio-read-untainted", methods=["GET"])
def iast_ast_patching_io_string_io_read_untainted():
    filename = "filename"
    style = request.args.get("style")
    if style == "_io_module":
        import _io

        changed = _io.StringIO(filename)
    elif style == "io_module":
        import io

        changed = io.StringIO(filename)
    elif style == "io_function":
        from io import StringIO

        changed = StringIO(filename)
    else:
        from _io import StringIO

        changed = StringIO(filename)
    resp = Response("Fail")
    if not is_pyobject_tainted(changed.read(4)):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-io-bytesio", methods=["GET"])
def iast_ast_patching_io_bytes_io():
    filename = request.args.get("filename")
    style = request.args.get("style")
    bytes_filename = filename.encode()
    if style == "_io_module":
        import _io

        changed = _io.BytesIO(bytes_filename)
    elif style == "io_module":
        import io

        changed = io.BytesIO(bytes_filename)
    elif style == "io_function":
        from io import BytesIO

        changed = BytesIO(bytes_filename)
    else:
        from _io import BytesIO

        changed = BytesIO(bytes_filename)
    resp = Response("Fail")
    if is_pyobject_tainted(changed):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-io-stringio", methods=["GET"])
def iast_ast_patching_io_string_io():
    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "_io_module":
        import _io

        changed = _io.StringIO(filename)
    elif style == "io_module":
        import io

        changed = io.StringIO(filename)
    elif style == "io_function":
        from io import StringIO

        changed = StringIO(filename)
    else:
        from _io import StringIO

        changed = StringIO(filename)
    resp = Response("Fail")
    if is_pyobject_tainted(changed):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-io-bytesio-read", methods=["GET"])
def iast_ast_patching_io_bytes_io_read():
    filename = request.args.get("filename")
    style = request.args.get("style")
    bytes_filename = filename.encode()
    if style == "_io_module":
        import _io

        changed = _io.BytesIO(bytes_filename)
    elif style == "io_module":
        import io

        changed = io.BytesIO(bytes_filename)
    elif style == "io_function":
        from io import BytesIO

        changed = BytesIO(bytes_filename)
    else:
        from _io import BytesIO

        changed = BytesIO(bytes_filename)
    resp = Response("Fail")
    if is_pyobject_tainted(changed.read(4)):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-io-stringio-read", methods=["GET"])
def iast_ast_patching_io_string_io_read():
    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "_io_module":
        import _io

        changed = _io.StringIO(filename)
    elif style == "io_module":
        import io

        changed = io.StringIO(filename)
    elif style == "io_function":
        from io import StringIO

        changed = StringIO(filename)
    else:
        from _io import StringIO

        changed = StringIO(filename)
    resp = Response("Fail")
    if is_pyobject_tainted(changed.read(4)):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-re-sub", methods=["GET"])
def iast_ast_patching_re_sub():
    filename = request.args.get("filename")
    style = request.args.get("style")
    changed = ""
    if style == "re_module":
        changed = re.sub(r"_", " ", filename)
    elif style == "re_object":
        pattern = re.compile(r"_")
        changed = pattern.sub(" ", filename)
    resp = Response("Fail")

    if is_pyobject_tainted(changed):
        resp = Response("OK")

    return resp


@app.route("/iast-ast-patching-non-re-sub", methods=["GET"])
def iast_ast_patching_non_re_sub():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    changed = ""
    if style == "re_module":
        changed = re.sub(r"_", " ", filename)
    elif style == "re_object":
        pattern = re.compile(r"_")
        changed = pattern.sub(" ", filename)
    resp = Response("OK")

    if is_pyobject_tainted(changed):
        resp = Response("Fail")
    return resp


@app.route("/iast-ast-patching-re-subn", methods=["GET"])
def iast_ast_patching_re_subn():
    filename = request.args.get("filename")
    style = request.args.get("style")
    changed = ""
    if style == "re_module":
        changed, number = re.subn(r"_", " ", filename)
    elif style == "re_object":
        pattern = re.compile(r"_")
        changed, number = pattern.subn(" ", filename)
    resp = Response("Fail")
    if is_pyobject_tainted(changed):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-non-re-subn", methods=["GET"])
def iast_ast_patching_non_re_subn():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    changed = ""
    if style == "re_module":
        changed, number = re.subn(r"_", " ", filename)
    elif style == "re_object":
        pattern = re.compile(r"_")
        changed, number = pattern.subn(" ", filename)
    resp = Response("OK")
    if is_pyobject_tainted(changed):
        resp = Response("Fail")

    return resp


@app.route("/iast-ast-patching-re-split", methods=["GET"])
def iast_ast_patching_re_split():
    filename = request.args.get("filename")
    style = request.args.get("style")
    result = ""
    if style == "re_module":
        result = re.split(r"_", filename)
    elif style == "re_object":
        pattern = re.compile(r"_")
        result = pattern.split(filename)
    resp = Response("Fail")
    if all(map(is_pyobject_tainted, result)):
        resp = Response("OK")

    return resp


@app.route("/iast-ast-patching-non-re-split", methods=["GET"])
def iast_ast_patching_non_re_split():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    result = ""
    if style == "re_module":
        result = re.split(r"_", filename)
    elif style == "re_object":
        pattern = re.compile(r"_")
        result = pattern.split(filename)
    resp = Response("OK")

    if any(map(is_pyobject_tainted, result)):
        resp = Response("Fail")
    return resp


@app.route("/iast-ast-patching-re-findall", methods=["GET"])
def iast_ast_patching_re_findall():
    filename = request.args.get("filename")
    style = request.args.get("style")
    result = ""
    if style == "re_module":
        result = re.findall(r"_[a-z]*", filename)
    elif style == "re_object":
        pattern = re.compile(r"_[a-z]*")
        result = pattern.findall(filename)
    resp = Response("Fail")

    if all(map(is_pyobject_tainted, result)):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-non-re-findall", methods=["GET"])
def iast_ast_patching_non_re_findall():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    result = ""
    if style == "re_module":
        result = re.findall(r"_[a-z]*", filename)
    elif style == "re_object":
        pattern = re.compile(r"_[a-z]*")
        result = pattern.findall(filename)
    resp = Response("OK")

    if any(map(is_pyobject_tainted, result)):
        resp = Response("Fail")

    return resp


@app.route("/iast-ast-patching-re-finditer", methods=["GET"])
def iast_ast_patching_re_finditer():
    filename = request.args.get("filename")
    style = request.args.get("style")
    result = ""
    if style == "re_module":
        result = re.finditer(r"_[a-z]*", filename)
    elif style == "re_object":
        pattern = re.compile(r"_[a-z]*")
        result = pattern.finditer(filename)
    resp = Response("Fail")

    if all(map(is_pyobject_tainted, result)):
        resp = Response("OK")

    return resp


@app.route("/iast-ast-patching-non-re-finditer", methods=["GET"])
def iast_ast_patching_non_re_finditer():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    result = ""
    if style == "re_module":
        result = re.finditer(r"_[a-z]*", filename)
    elif style == "re_object":
        pattern = re.compile(r"_[a-z]*")
        result = pattern.finditer(filename)
    resp = Response("OK")
    if any(map(is_pyobject_tainted, result)):
        resp = Response("Fail")
    return resp


@app.route("/iast-ast-patching-re-groups", methods=["GET"])
def iast_ast_patching_re_groups():
    filename = request.args.get("filename")
    style = request.args.get("style")
    result = ""
    if style == "re_module":
        re_match = re.match(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.match(filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    resp = Response("Fail")

    if result and all(map(is_pyobject_tainted, result)):
        resp = Response("OK")

    return resp


@app.route("/iast-ast-patching-non-re-groups", methods=["GET"])
def iast_ast_patching_non_re_groups():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    result = ""
    if style == "re_module":
        re_match = re.match(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.match(filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    resp = Response("OK")

    if not result or any(map(is_pyobject_tainted, result)):
        resp = Response("Fail")

    return resp


@app.route("/iast-ast-patching-re-string", methods=["GET"])
def iast_ast_patching_re_string():
    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "re_module":
        re_match = re.match(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.string
        else:
            result = None
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.match(filename)
        if re_match is not None:
            result = re_match.string
        else:
            result = None
    resp = Response("Fail")
    if result and is_pyobject_tainted(result):
        resp = Response("OK")
    return resp


@app.route("/iast-ast-patching-non-re-string", methods=["GET"])
def iast_ast_patching_non_re_string():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "re_module":
        re_match = re.match(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.string
        else:
            result = None
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.match(filename)
        if re_match is not None:
            result = re_match.string
        else:
            result = None
    resp = Response("OK")
    if not result or is_pyobject_tainted(result):
        resp = Response("Fail")

    return resp


@app.route("/iast-ast-patching-re-fullmatch", methods=["GET"])
def iast_ast_patching_re_fullmatch():
    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "re_module":
        re_match = re.fullmatch(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.fullmatch(filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    resp = Response("Fail")
    if result and all(map(is_pyobject_tainted, result)):
        resp = Response("OK")

    return resp


@app.route("/iast-ast-patching-non-re-fullmatch", methods=["GET"])
def iast_ast_patching_non_re_fullmatch():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "re_module":
        re_match = re.fullmatch(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.fullmatch(filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    resp = Response("OK")

    if not result or any(map(is_pyobject_tainted, result)):
        resp = Response("Fail")

    return resp


@app.route("/iast-ast-patching-re-expand", methods=["GET"])
def iast_ast_patching_re_expand():
    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "re_module":
        re_match = re.search(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.expand(r"Hello, \1!")
        else:
            result = None
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.search(filename)
        if re_match is not None:
            result = re_match.expand(r"Hello, \1!")
        else:
            result = None
    resp = Response("Fail")

    if result and is_pyobject_tainted(result):
        resp = Response("OK")

    return resp


@app.route("/iast-ast-patching-non-re-expand", methods=["GET"])
def iast_ast_patching_non_re_expand():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "re_module":
        re_match = re.search(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.expand(r"Hello, \1!")
        else:
            result = None
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.search(filename)
        if re_match is not None:
            result = re_match.expand(r"Hello, \1!")
        else:
            result = None
    resp = Response("OK")

    if not result or is_pyobject_tainted(result):
        resp = Response("Fail")

    return resp


@app.route("/iast-ast-patching-re-search", methods=["GET"])
def iast_ast_patching_re_search():
    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "re_module":
        re_match = re.search(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.search(filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    resp = Response("Fail")

    if result and all(map(is_pyobject_tainted, result)):
        resp = Response("OK")

    return resp


@app.route("/iast-ast-patching-non-re-search", methods=["GET"])
def iast_ast_patching_non_re_search():
    import iast.fixtures.non_re_module as re

    filename = request.args.get("filename")
    style = request.args.get("style")
    if style == "re_module":
        re_match = re.search(r"(\w+) (\w+)", filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    elif style == "re_object":
        pattern = re.compile(r"(\w+) (\w+)")
        re_match = pattern.search(filename)
        if re_match is not None:
            result = re_match.groups()
        else:
            result = []
    resp = Response("OK")

    if not result or any(map(is_pyobject_tainted, result)):
        resp = Response("Fail")

    return resp


@app.route("/common-modules-patch-read", methods=["GET"])
def test_flask_common_modules_patch_read():
    copy_open = copy.deepcopy(open)
    return Response(f"OK: {isinstance(copy_open, FunctionWrapper)}")


if __name__ == "__main__":
    env_port = os.getenv("FLASK_RUN_PORT", 8000)
    debug = asbool(os.getenv("FLASK_DEBUG", "false"))
    ddtrace_iast_flask_patch()
    app.run(debug=debug, port=env_port)
