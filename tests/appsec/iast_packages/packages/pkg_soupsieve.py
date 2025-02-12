"""
soupsieve==2.5

https://pypi.org/project/soupsieve/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_soupsieve = Blueprint("package_soupsieve", __name__)


@pkg_soupsieve.route("/soupsieve")
def pkg_soupsieve_view():
    from bs4 import BeautifulSoup
    import soupsieve as sv

    response = ResultResponse(request.args.get("package_param"))

    try:
        html_content = request.args.get("package_param", "<div><p>Example paragraph</p></div>")

        try:
            soup = BeautifulSoup(html_content, "html.parser")
            paragraphs = sv.select("p", soup)
            result_output = f"Found {len(paragraphs)} paragraph(s): " + ", ".join([p.text for p in paragraphs])
        except sv.SelectorSyntaxError as e:
            result_output = f"Selector syntax error: {str(e)}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()


@pkg_soupsieve.route("/soupsieve_propagation")
def pkg_soupsieve_propagation_view():
    from bs4 import BeautifulSoup
    import soupsieve as sv

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        html_content = request.args.get("package_param", "<div><p>Example paragraph</p></div>")

        try:
            soup = BeautifulSoup(html_content, "html.parser")
            paragraphs = sv.select("p", soup)
            joined = ", ".join([p.text for p in paragraphs])
            result_output = "OK" if is_pyobject_tainted(joined) else f"Error: paragraphs are not tainted: {joined}"
        except sv.SelectorSyntaxError as e:
            result_output = f"Selector syntax error: {str(e)}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
