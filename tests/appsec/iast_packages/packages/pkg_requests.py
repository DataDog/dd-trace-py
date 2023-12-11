"""
requests==2.31.0

https://pypi.org/project/requests/
"""
from flask import Blueprint
from flask import request
import requests

from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted


pkg_requests = Blueprint("package_requests", __name__)


@pkg_requests.route("/requests")
def pkg_requests_view():
    package_param = request.args.get("package_param")
    try:
        requests.get("http://" + package_param)
    except Exception:
        pass
    return {"param": package_param, "params_are_tainted": is_pyobject_tainted(package_param)}
