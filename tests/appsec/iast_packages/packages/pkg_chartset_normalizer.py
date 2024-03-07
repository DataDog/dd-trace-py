"""
charset-normalizer==3.3.2

https://pypi.org/project/charset-normalizer/
"""
from charset_normalizer import from_bytes
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_chartset_normalizer = Blueprint("package_chartset_normalizer", __name__)


@pkg_chartset_normalizer.route("/charset-normalizer")
def pkg_idna_view():
    response = ResultResponse(request.args.get("package_param"))
    response.result1 = str(from_bytes(bytes(response.package_param, encoding="utf-8")).best())
    return response.json()
