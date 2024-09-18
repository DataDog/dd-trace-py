"""
psutil==5.9.8

https://pypi.org/project/psutil/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_psutil = Blueprint("package_psutil", __name__)


@pkg_psutil.route("/psutil")
def pkg_psutil_view():
    import psutil

    response = ResultResponse(request.args.get("package_param"))

    try:
        _ = request.args.get("package_param", "cpu")

        try:
            _ = psutil.cpu_percent(interval=1)
            result_output = "CPU Usage: replaced_usage"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
