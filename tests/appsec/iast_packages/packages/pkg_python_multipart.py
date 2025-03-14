"""
isodate==0.6.1

https://pypi.org/project/isodate/
"""

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_python_multipart = Blueprint("multipart", __name__)


@pkg_python_multipart.route("/python-multipart")
def pkg_multipart_view():
    from multipart.multipart import parse_options_header

    response = ResultResponse(request.args.get("package_param"))

    try:
        _, params = parse_options_header(response.package_param)

        response.result1 = str(params[b"boundary"], "utf-8")
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()


@pkg_python_multipart.route("/python-multipart_propagation")
def pkg_multipart_propagation_view():
    from multipart.multipart import parse_options_header

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    _, params = parse_options_header(response.package_param)
    response.result1 = (
        "OK"
        if is_pyobject_tainted(params[b"boundary"])
        else "Error: yaml_string is not tainted: %s" % str(params[b"boundary"], "utf-8")
    )
    return response.json()
