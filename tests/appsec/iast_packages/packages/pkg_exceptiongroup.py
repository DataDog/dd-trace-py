"""
exceptiongroup==1.2.1

https://pypi.org/project/exceptiongroup/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_exceptiongroup = Blueprint("package_exceptiongroup", __name__)


@pkg_exceptiongroup.route("/exceptiongroup")
def pkg_exceptiongroup_view():
    from exceptiongroup import ExceptionGroup

    response = ResultResponse(request.args.get("package_param"))

    try:
        package_param = request.args.get("package_param", "default message")

        def raise_exceptions(param):
            raise ExceptionGroup(
                "Multiple errors", [ValueError(f"First error with {param}"), TypeError(f"Second error with {param}")]
            )

        try:
            raise_exceptions(package_param)
        except ExceptionGroup as eg:
            caught_exceptions = eg

        if caught_exceptions:
            result_output = "\n".join(f"{type(ex).__name__}: {str(ex)}" for ex in caught_exceptions.exceptions)
        else:
            result_output = "No exceptions caught"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"
    return response.json()


@pkg_exceptiongroup.route("/exceptiongroup_propagation")
def pkg_exceptiongroup_propagation_view():
    from exceptiongroup import ExceptionGroup

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    try:
        package_param = request.args.get("package_param", "default message")

        if not is_pyobject_tainted(package_param):
            response.result1 = "Error: package_param is not tainted"
            return response.json()

        def raise_exceptions(param):
            raise ExceptionGroup(
                "Multiple errors", [ValueError(f"First error with {param}"), TypeError(f"Second error with {param}")]
            )

        try:
            raise_exceptions(package_param)
        except ExceptionGroup as eg:
            caught_exceptions = eg

        if caught_exceptions:
            result_output = "\n".join(f"{type(ex).__name__}: {str(ex)}" for ex in caught_exceptions.exceptions)
        else:
            result_output = "Error: No exceptions caught"

        response.result1 = (
            "OK" if is_pyobject_tainted(package_param) else "Error: result is not tainted: %s" % result_output
        )
    except Exception as e:
        response.result1 = f"Error: {str(e)}"
    return response.json()
