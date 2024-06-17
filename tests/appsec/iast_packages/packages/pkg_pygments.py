"""
Pygments==2.18.0

https://pypi.org/project/Pygments/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_pygments = Blueprint("package_pygments", __name__)


@pkg_pygments.route("/pygments")
def pkg_pygments_view():
    from pygments import highlight
    from pygments.formatters import HtmlFormatter
    from pygments.lexers import PythonLexer

    response = ResultResponse(request.args.get("package_param"))

    try:
        code = request.args.get("package_param", "print('Hello, world!')")

        try:
            lexer = PythonLexer()
            formatter = HtmlFormatter()
            highlighted_code = highlight(code, lexer, formatter)
            result_output = highlighted_code
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
