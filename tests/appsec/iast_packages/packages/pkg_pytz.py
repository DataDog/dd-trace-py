"""
pytz==2024.1

https://pypi.org/project/pytz/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_pytz = Blueprint("package_pytz", __name__)


@pkg_pytz.route("/pytz")
def pkg_pytz_view():
    from datetime import datetime

    import pytz

    response = ResultResponse(request.args.get("package_param"))

    try:
        timezone_param = request.args.get("package_param", "UTC")

        try:
            timezone = pytz.timezone(timezone_param)
            current_time = datetime.now(timezone)
            _ = current_time.strftime("%Y-%m-%d %H:%M:%S")
            # Use a constant string for reproducibility
            result_output = f"Current time in {timezone_param}: replaced_time"
        except pytz.UnknownTimeZoneError:
            result_output = f"Unknown timezone: {timezone_param}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
