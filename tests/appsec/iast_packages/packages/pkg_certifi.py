"""
certifi==2024.2.2

https://pypi.org/project/certifi/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_certifi = Blueprint("package_certifi", __name__)


@pkg_certifi.route("/certifi")
def pkg_certifi_view():
    import certifi

    response = ResultResponse(request.args.get("package_param"))

    try:
        ca_bundle_path = certifi.where()
        response.result1 = f"The path to the CA bundle is: {ca_bundle_path}"
    except Exception as e:
        response.result1 = str(e)

    return response.json()
