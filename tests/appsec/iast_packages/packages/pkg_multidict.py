"""
multidict==6.0.5

https://pypi.org/project/multidict/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_multidict = Blueprint("package_multidict", __name__)


@pkg_multidict.route("/multidict")
def pkg_multidict_view():
    from multidict import MultiDict

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "key1=value1&key2=value2")
        items = [item.split("=") for item in param_value.split("&")]
        multi_dict = MultiDict(items)

        result_output = f"MultiDict contents: {dict(multi_dict)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()


@pkg_multidict.route("/multidict_propagation")
def pkg_multidict_propagation_view():
    from multidict import MultiDict

    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        param_value = request.args.get("package_param", "key1=value1&key2=value2")
        items = [item.split("=") for item in param_value.split("&")]
        multi_dict = MultiDict(items)
        response.result1 = (
            "OK" if is_pyobject_tainted(multi_dict["key1"]) else "Error: multi_dict is not tainted: %s" % multi_dict
        )
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
