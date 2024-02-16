"""
idna==3.6

https://pypi.org/project/idna/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_idna = Blueprint("package_idna", __name__)


@pkg_idna.route("/idna")
def pkg_idna_view():
    import idna

    response = ResultResponse(request.args.get("package_param"))

    response.result1 = idna.decode(response.package_param)
    response.result2 = str(idna.encode(response.result1), encoding="utf-8")
    return response.json()
