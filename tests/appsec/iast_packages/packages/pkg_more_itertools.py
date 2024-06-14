"""
more-itertools==10.2.0

https://pypi.org/project/more-itertools/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_more_itertools = Blueprint("package_more_itertools", __name__)


@pkg_more_itertools.route("/more-itertools")
def pkg_more_itertools_view():
    import more_itertools as mit

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "1,2,3,4,5")
        sequence = [int(x) for x in param_value.split(",")]

        grouped = list(mit.chunked(sequence, 2))
        result_output = f"Chunked sequence: {grouped}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
