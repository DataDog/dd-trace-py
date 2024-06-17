"""
decorator==5.1.1

https://pypi.org/project/decorator/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_decorator = Blueprint("package_decorator", __name__)


@pkg_decorator.route("/decorator")
def pkg_decorator_view():
    from decorator import decorator

    response = ResultResponse(request.args.get("package_param"))

    @decorator
    def my_decorator(func, *args, **kwargs):
        return f"Decorated result: {func(*args, **kwargs)}"

    @my_decorator
    def greet(name):
        return f"Hello, {name}!"

    try:
        param_value = request.args.get("package_param", "World")

        try:
            # Call the decorated function
            result_output = greet(param_value)
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output.replace("\n", "\\n").replace('"', '\\"').replace("'", "\\'")
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
