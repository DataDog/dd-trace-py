"""
platformdirs==4.2.2

https://pypi.org/project/platformdirs/
"""
import os

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_platformdirs = Blueprint("package_platformdirs", __name__)


@pkg_platformdirs.route("/platformdirs")
def pkg_platformdirs_view():
    from platformdirs import user_data_dir

    response = ResultResponse(request.args.get("package_param"))

    try:
        app_name = request.args.get("package_param", "default-app")

        # Get the user data directory for the application
        data_dir = user_data_dir(app_name)

        # Create the directory if it doesn't exist
        if not os.path.exists(data_dir):
            os.makedirs(data_dir)

        result_output = f"User data directory for {app_name}: {data_dir}"

        # Clean up the created directory
        if os.path.exists(data_dir):
            os.rmdir(data_dir)

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()


@pkg_platformdirs.route("/platformdirs_propagation")
def pkg_platformdirs_propagation_view():
    from platformdirs import user_data_dir

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        app_name = request.args.get("package_param", "default-app")
        data_dir = user_data_dir(app_name)
        response.result1 = "OK" if is_pyobject_tainted(data_dir) else f"Error: data_dir is not tainted: {data_dir}"
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
