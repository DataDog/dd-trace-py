"""
beautifulsoup4==4.12.3

https://pypi.org/project/beautifulsoup4/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_beautifulsoup4 = Blueprint("package_beautifulsoup4", __name__)


@pkg_beautifulsoup4.route("/beautifulsoup4")
def pkg_beautifulsoup4_view():
    from bs4 import BeautifulSoup

    response = ResultResponse(request.args.get("package_param"))

    try:
        html = response.package_param
        "".join(BeautifulSoup(html, features="lxml").findAll(string=True)).lstrip()
    except Exception:
        pass
    return response.json()


@pkg_beautifulsoup4.route("/beautifulsoup4_propagation")
def pkg_beautifulsoup4_propagation_view():
    from bs4 import BeautifulSoup

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        html = response.package_param
        soup = BeautifulSoup(html, "html.parser")
        html_tags = soup.find_all("html")
        output = "".join(str(tag) for tag in html_tags).lstrip()
        if not is_pyobject_tainted(output):
            response.result1 = "Error: output is not tainted: " + str(output)
            return response.json()
        response.result1 = "OK"
    except Exception as e:
        response.result1 = "Exception: " + str(e)

    return response.json()
