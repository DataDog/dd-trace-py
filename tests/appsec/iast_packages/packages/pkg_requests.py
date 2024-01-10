"""
requests==2.31.0

https://pypi.org/project/requests/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_requests = Blueprint("package_requests", __name__)


@pkg_requests.route("/requests")
def pkg_requests_view():
    import requests

    response = ResultResponse(request.args.get("package_param"))
    try:
        requests.get("http://" + response.package_param)
    except Exception:
        pass
    return response.json()
