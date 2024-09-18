"""
urllib3==2.31.0

https://pypi.org/project/urllib3/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_urllib3 = Blueprint("package_urllib3", __name__)


@pkg_urllib3.route("/urllib3")
def pkg_urllib3_view():
    import urllib3

    response = ResultResponse(request.args.get("package_param"))
    response.result1 = urllib3.util.parse_url(response.package_param)
    response.result2 = response.result1.host
    try:
        urllib3.request("GET", "http://" + response.package_param)
    except Exception:
        pass
    return response.json()
