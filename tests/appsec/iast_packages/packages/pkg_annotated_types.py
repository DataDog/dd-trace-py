"""
annotated-types==0.7.0

https://pypi.org/project/annotated-types/
"""

from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_annotated_types = Blueprint("package_annotated_types", __name__)


@pkg_annotated_types.route("/annotated-types")
def pkg_annotated_types_view():
    from typing import Annotated

    from annotated_types import Gt

    response = ResultResponse(request.args.get("package_param"))

    def process_value(value: Annotated[int, Gt(10)]):
        return f"Processed value: {value}"

    try:
        param_value = int(request.args.get("package_param", "15"))

        try:
            result_output = process_value(param_value)
        except ValueError as e:
            result_output = f"Error: Value must be greater than 10. {str(e)}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output.replace("\n", "\\n").replace('"', '\\"').replace("'", "\\'")
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
