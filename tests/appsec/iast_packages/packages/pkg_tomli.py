"""
tomli==2.0.1

https://pypi.org/project/tomli/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_tomli = Blueprint("package_tomli", __name__)


@pkg_tomli.route("/tomli")
def pkg_tomli_view():
    import tomli

    response = ResultResponse(request.args.get("package_param"))

    try:
        tomli_data = request.args.get("package_param", "key = 'value'")

        try:
            data = tomli.loads(tomli_data)
            result_output = f"Parsed TOML data: {data}"
        except tomli.TOMLDecodeError as e:
            result_output = f"TOML decoding error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()


@pkg_tomli.route("/tomli_propagation")
def pkg_tomli_propagation_view():
    import tomli

    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        tomli_data = request.args.get("package_param", "key = 'value'")

        try:
            data = tomli.loads(tomli_data)
            value = data["key"]
            response.result1 = "OK" if is_pyobject_tainted(value) else f"Error: data is not tainted: {value}"
        except tomli.TOMLDecodeError as e:
            response.result1 = f"TOML decoding error: {str(e)}"
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
