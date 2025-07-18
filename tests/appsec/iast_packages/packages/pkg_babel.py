"""
idna==3.6

https://pypi.org/project/idna/
"""

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_babel = Blueprint("package_babel", __name__)


@pkg_babel.route("/babel")
def pkg_babel_view():
    from babel.plural import to_python

    response = ResultResponse(request.args.get("package_param"))
    func = to_python({"one": "n is 1", "few": "n in 2..4"})

    response.result1 = func(int(request.args.get("package_param")))
    return response.json()


@pkg_babel.route("/babel_propagation")
def pkg_babel_propagation_view():
    from babel import Locale

    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    response.result1 = Locale("en", "US").currency_formats["standard"]
    response.result1 = "OK" if is_pyobject_tainted(response.package_param) else "Error: result is not tainted"
    return response.json()
