"""
beautifulsoup4==4.12.3

https://pypi.org/project/beautifulsoup4/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_beautifulsoup4 = Blueprint("package_beautifulsoup4", __name__)


@pkg_beautifulsoup4.route("/beautifulsoup4")
def pkg_beautifusoup4_view():
    from bs4 import BeautifulSoup

    response = ResultResponse(request.args.get("package_param"))

    try:
        html = response.package_param
        "".join(BeautifulSoup(html, features="lxml").findAll(string=True)).lstrip()
    except Exception:
        pass
    return response.json()
