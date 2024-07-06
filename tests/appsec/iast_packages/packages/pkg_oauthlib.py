"""
oauthlib==3.2.2

https://pypi.org/project/oauthlib/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_oauthlib = Blueprint("package_oauthlib", __name__)


@pkg_oauthlib.route("/oauthlib")
def pkg_oauthlib_view():
    from oauthlib.oauth2 import BackendApplicationClient
    from oauthlib.oauth2 import OAuth2Error

    response = ResultResponse(request.args.get("package_param"))
    try:
        client_id = request.args.get("package_param", "default-client-id")

        try:
            _ = BackendApplicationClient(client_id=client_id)
            result_output = f"OAuth2 client created with client ID: {client_id}"
        except OAuth2Error as e:
            result_output = f"OAuth2 error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
