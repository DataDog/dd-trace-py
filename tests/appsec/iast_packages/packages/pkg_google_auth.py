"""
google-auth==2.29.0

https://pypi.org/project/google-auth/
"""

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_google_auth = Blueprint("package_google_auth", __name__)


@pkg_google_auth.route("/google-auth")
def pkg_google_auth():
    response = ResultResponse(request.args.get("package_param"))

    try:
        from google.auth.crypt import rsa

        rsa.RSASigner.sign("test")
    except Exception as e:
        response.result1 = str(e)

    return response.json()
