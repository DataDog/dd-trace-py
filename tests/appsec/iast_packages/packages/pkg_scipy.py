"""
scipy==1.13.0

https://pypi.org/project/scipy/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_scipy = Blueprint("package_scipy", __name__)


@pkg_scipy.route("/scipy")
def pkg_scipy_view():
    import scipy.stats as stats

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "1,2,3,4,5")

        try:
            # Convert the input string to a list of numbers
            data = list(map(float, param_value.split(",")))

            # Calculate mean and standard deviation
            mean = stats.tmean(data)
            std_dev = stats.tstd(data)
            result_output = f"Mean: {mean}, Standard Deviation: {round(std_dev, 3)}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
