import sys

from Crypto.Cipher import AES
from Crypto.Cipher import ARC4
from flask import Flask
from flask import request

from ddtrace import tracer
from ddtrace.appsec._constants import IAST
from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted
from ddtrace.internal import core


class ResultResponse:
    param = ""
    sources = ""
    vulnerabilities = ""

    def __init__(self, param):
        self.param = param

    def json(self):
        return {
            "param": self.param,
            "sources": self.sources,
            "vulnerabilities": self.vulnerabilities,
            "params_are_tainted": is_pyobject_tainted(self.param),
        }


def create_app():
    app = Flask(__name__)

    @app.route("/shutdown")
    def shutdown():
        tracer.shutdown()
        sys.exit(0)

    @app.route("/")
    def secure_weak_cipher():
        param = request.args.get("param", "param")

        key = b"Sixteen byte key"
        data = b"abcdefgh"
        crypt_obj = AES.new(key, AES.MODE_EAX)
        crypt_obj.encrypt(data)

        response = ResultResponse(param)
        report = core.get_items([IAST.CONTEXT_KEY], tracer.current_root_span())
        if report and report[0]:
            response.sources = report[0].sources[0].value
            response.vulnerabilities = list(report[0].vulnerabilities)[0].type

        return response.json()

    @app.route("/weak_cipher")
    def insecure_weak_cipher():
        param = request.args.get("param", "param")

        password = b"12345678"
        data = b"abcdefgh"
        crypt_obj = ARC4.new(password)
        crypt_obj.encrypt(data)

        response = ResultResponse(param)
        report = core.get_items([IAST.CONTEXT_KEY], tracer.current_root_span())
        if report and report[0]:
            response.sources = report[0].sources[0].value if report[0].sources else ""
            response.vulnerabilities = list(report[0].vulnerabilities)[0].type

        return response.json()

    return app
