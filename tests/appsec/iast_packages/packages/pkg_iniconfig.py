"""
iniconfig==2.0.0

https://pypi.org/project/iniconfig/
"""
import os

from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_iniconfig = Blueprint("package_iniconfig", __name__)


@pkg_iniconfig.route("/iniconfig")
def pkg_iniconfig_view():
    import iniconfig

    response = ResultResponse(request.args.get("package_param"))

    try:
        value = request.args.get("package_param", "test1234")
        ini_content = f"[section]\nkey={value}"
        ini_path = "example.ini"

        try:
            with open(ini_path, "w") as f:
                f.write(ini_content)

            config = iniconfig.IniConfig(ini_path)
            parsed_data = {section.name: list(section.items()) for section in config}
            result_output = f"Parsed INI data: {parsed_data}"

            if os.path.exists(ini_path):
                os.remove(ini_path)
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())


@pkg_iniconfig.route("/iniconfig_propagation")
def pkg_iniconfig_propagation_view():
    import iniconfig

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    try:
        value = request.args.get("package_param", "test1234")
        if not is_pyobject_tainted(value):
            response.result1 = "Error: package_param is not tainted"
            return jsonify(response.json())

        ini_content = f"[section]\nkey={value}"
        if not is_pyobject_tainted(ini_content):
            response.result1 = f"Error: combined ini_content is not tainted: {ini_content}"
            return jsonify(response.json())

        ini_path = "example.ini"

        try:
            config = iniconfig.IniConfig(ini_path, data=ini_content)
            read_value = config["section"]["key"]
            result_output = (
                "OK"
                if is_pyobject_tainted(read_value)
                else f"Error: read_value from parsed_data is not tainted: {read_value}"
            )
        except Exception as e:
            result_output = f"Error: {str(e)}"
    except Exception as e:
        result_output = f"Error: {str(e)}"

    response.result1 = result_output

    return jsonify(response.json())
