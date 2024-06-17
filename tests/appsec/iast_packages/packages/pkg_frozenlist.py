"""
frozenlist==1.4.1

https://pypi.org/project/frozenlist/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_frozenlist = Blueprint("package_frozenlist", __name__)


@pkg_frozenlist.route("/frozenlist")
def pkg_frozenlist_view():
    from frozenlist import FrozenList

    response = ResultResponse(request.args.get("package_param"))

    try:
        input_values = request.args.get("package_param", "1,2,3")
        values = list(map(int, input_values.split(",")))

        try:
            # Create a FrozenList and perform operations
            fl = FrozenList(values)
            fl.freeze()  # Make the list immutable
            result_output = f"Original list: {fl}"

            try:
                fl.append(4)  # This should raise an error because the list is frozen
            except RuntimeError:
                result_output += " Attempt to modify frozen list!"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
