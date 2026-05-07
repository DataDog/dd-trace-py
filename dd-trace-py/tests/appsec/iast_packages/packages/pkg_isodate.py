"""
isodate==0.6.1

https://pypi.org/project/isodate/
"""

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_isodate = Blueprint("package_isodate", __name__)


@pkg_isodate.route("/isodate")
def pkg_isodate_view():
    import isodate

    response = ResultResponse(request.args.get("package_param"))

    try:
        iso_string = request.args.get("package_param", "2023-06-15T13:45:30")

        try:
            parsed_date = isodate.parse_datetime(iso_string)
            result_output = f"Parsed date and time: {parsed_date}"
        except isodate.ISO8601Error:
            result_output = f"Invalid ISO8601 date/time string: {iso_string}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
