"""
pyparsing==3.1.2

https://pypi.org/project/pyparsing/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_pyparsing = Blueprint("package_pyparsing", __name__)


@pkg_pyparsing.route("/pyparsing")
def pkg_pyparsing_view():
    import pyparsing as pp

    response = ResultResponse(request.args.get("package_param"))

    try:
        input_string = request.args.get("package_param", "123-456-7890")

        try:
            # Define a simple grammar to parse a phone number
            integer = pp.Word(pp.nums)
            dash = pp.Suppress("-")
            phone_number = integer + dash + integer + dash + integer

            # Parse the input string
            parsed = phone_number.parseString(input_string)
            result_output = f"Parsed phone number: {parsed.asList()}"
        except pp.ParseException as e:
            result_output = f"Parse error: {str(e)}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())


@pkg_pyparsing.route("/pyparsing_propagation")
def pkg_pyparsing_propagation_view():
    import pyparsing as pp

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))

    try:
        input_string = request.args.get("package_param", "123-456-7890")
        if not is_pyobject_tainted(input_string):
            response.result1 = "Error: package_param is not tainted"
            return jsonify(response.json())

        try:
            integer = pp.Word(pp.nums)
            dash = pp.Suppress("-")
            phone_number = integer + dash + integer + dash + integer
            parsed = phone_number.parseString(input_string)
            result_output = "OK"
            for item in parsed:
                if not is_pyobject_tainted(item):
                    result_output = f"Error: item '{item}' from pyparsed result {parsed} is not tainted"
                    break
        except pp.ParseException as e:
            result_output = f"Parse error: {str(e)}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

    except Exception as e:
        result_output = f"Error: {str(e)}"

    response.result1 = result_output
    return jsonify(response.json())
