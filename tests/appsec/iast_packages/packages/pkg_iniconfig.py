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
        # Not using the argument for this one because it eats the newline characters
        ini_content = "[section]\nkey=value"
        # ini_content = request.args.get("package_param", "[section]\nkey=value")
        ini_path = "example.ini"

        try:
            # Write the ini content to a file
            with open(ini_path, "w") as f:
                f.write(ini_content)

            # Read and parse the ini file
            config = iniconfig.IniConfig(ini_path)
            parsed_data = {section.name: list(section.items()) for section in config}
            result_output = f"Parsed INI data: {parsed_data}"

            # Clean up the created ini file
            if os.path.exists(ini_path):
                os.remove(ini_path)
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
