"""
wrapt==1.16.0

https://pypi.org/project/wrapt/
"""

from flask import Blueprint
from flask import jsonify
from flask import request
import wrapt

from .utils import ResultResponse


pkg_wrapt = Blueprint("package_wrapt", __name__)


# Decorator to log function calls
@wrapt.decorator
def log_function_call(wrapped, instance, args, kwargs):
    print(f"Function '{wrapped.__name__}' was called with args: {args} and kwargs: {kwargs}")
    return wrapped(*args, **kwargs)


@pkg_wrapt.route("/wrapt")
def pkg_wrapt_view():
    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-value")

        @log_function_call
        def sample_function(param):
            return f"Function executed with param: {param}"

        try:
            result_output = sample_function(param_value)
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())


@pkg_wrapt.route("/wrapt_propagation")
def pkg_wrapt_propagation_view():
    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default-value")

        @log_function_call
        def sample_function(param):
            return f"Function executed with param: {param}"

        try:
            res = sample_function(param_value)
            result_output = "OK" if is_pyobject_tainted(res) else f"Error: result is not tainted: {res}"

        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
