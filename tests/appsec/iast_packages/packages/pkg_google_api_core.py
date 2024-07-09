"""
google-api-core==2.19.0

https://pypi.org/project/google-api-core/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_google_api_core = Blueprint("package_google_api_core", __name__)


@pkg_google_api_core.route("/google-api-core")
def pkg_google_api_core_view():
    response = ResultResponse(request.args.get("package_param"))

    try:
        from google.auth import credentials
        from google.auth.exceptions import DefaultCredentialsError

        try:
            credentials.Credentials()
        except DefaultCredentialsError:
            response.result1 = "No credentials"
    except Exception as e:
        response.result1 = str(e)

    return response.json()
