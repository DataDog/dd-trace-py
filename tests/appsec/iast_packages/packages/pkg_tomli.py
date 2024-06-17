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
