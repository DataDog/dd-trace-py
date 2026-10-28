"""
platformdirs==4.2.2

https://pypi.org/project/platformdirs/
"""

import contextlib
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

        # The path derives from the app name, so every xdist worker shares it and two requests
        # can create and remove it concurrently.
        os.makedirs(data_dir, exist_ok=True)

        result_output = f"User data directory for {app_name}: {data_dir}"

        # Clean up the created directory; another worker may have removed it already.
        with contextlib.suppress(FileNotFoundError):
            os.rmdir(data_dir)

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()


@pkg_platformdirs.route("/platformdirs_propagation")
def pkg_platformdirs_propagation_view():
    from platformdirs import user_data_dir

    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

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
