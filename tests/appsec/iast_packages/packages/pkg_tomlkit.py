"""
tomlkit==0.12.5

https://pypi.org/project/tomlkit/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_tomlkit = Blueprint("package_tomlkit", __name__)


@pkg_tomlkit.route("/tomlkit")
def pkg_tomlkit_view():
    import tomlkit

    response = ResultResponse(request.args.get("package_param"))

    try:
        toml_data = request.args.get("package_param", "key = 'value'")

        try:
            parsed_toml = tomlkit.loads(toml_data)
            result_output = f"Parsed TOML data: {parsed_toml}"
        except tomlkit.exceptions.TomlDecodeError as e:
            result_output = f"TOML decoding error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
