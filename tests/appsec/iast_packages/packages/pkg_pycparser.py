"""
pycparser==2.22
https://pypi.org/project/pycparser/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_pycparser = Blueprint("package_pycparser", __name__)


@pkg_pycparser.route("/pycparser")
def pkg_pycparser_view():
    import pycparser

    response = ResultResponse(request.args.get("package_param"))

    try:
        parser = pycparser.CParser()
        ast = parser.parse("int main() { return 0; }")

        response.result1 = str(ast)
    except Exception as e:
        response.result1 = str(e)

    return response.json()
