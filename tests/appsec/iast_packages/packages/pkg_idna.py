"""
idna==3.6

https://pypi.org/project/idna/
"""

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_idna = Blueprint("package_idna", __name__)


@pkg_idna.route("/idna")
def pkg_idna_view():
    import idna

    response = ResultResponse(request.args.get("package_param"))

    response.result1 = idna.decode(response.package_param)
    response.result2 = str(idna.encode(response.result1), encoding="utf-8")
    return response.json()


@pkg_idna.route("/idna_propagation")
def pkg_idna_propagation_view():
    import idna

    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    response.result1 = idna.decode(response.package_param)
    res = str(idna.encode(response.result1), encoding="utf-8")
    response.result1 = "OK" if is_pyobject_tainted(res) else "Error: result is not tainted"
    return response.json()
