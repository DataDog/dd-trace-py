"""
yarl==1.9.4

https://pypi.org/project/yarl/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_yarl = Blueprint("package_yarl", __name__)


@pkg_yarl.route("/yarl")
def pkg_yarl_view():
    from yarl import URL

    response = ResultResponse(request.args.get("package_param"))

    try:
        url_param = request.args.get("package_param", "https://example.com/path?query=param")

        try:
            url = URL(url_param)
            result_output = (
                f"Original URL: {url}\n"
                f"Scheme: {url.scheme}\n"
                f"Host: {url.host}\n"
                f"Path: {url.path}\n"
                f"Query: {url.query}\n"
            )
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()


@pkg_yarl.route("/yarl_propagation")
def pkg_yarl_propagation_view():
    from yarl import URL

    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        url_param = request.args.get("package_param", "https://example.com/path?query=param")

        try:
            url = URL(url_param)
            result_output = "OK" if is_pyobject_tainted(url.host) else f"Error: url.host is not tainted: {url.host}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
