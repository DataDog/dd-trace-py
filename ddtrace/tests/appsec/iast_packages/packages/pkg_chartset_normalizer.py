"""
charset-normalizer==3.3.2

https://pypi.org/project/charset-normalizer/
"""

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_chartset_normalizer = Blueprint("package_chartset_normalizer", __name__)


@pkg_chartset_normalizer.route("/charset-normalizer")
def pkg_charset_normalizer_view():
    from charset_normalizer import from_bytes

    response = ResultResponse(request.args.get("package_param"))
    response.result1 = str(from_bytes(bytes(response.package_param, encoding="utf-8")).best())
    return response.json()


@pkg_chartset_normalizer.route("/charset-normalizer_propagation")
def pkg_charset_normalizer_propagation_view():
    from charset_normalizer import from_bytes

    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        res = str(from_bytes(bytes(response.package_param, encoding="utf-8")).best())
        response.result1 = "OK" if is_pyobject_tainted(res) else "Error: result is not tainted: %s" % res
    except Exception as e:
        response.result1 = str(e)

    return response.json()
