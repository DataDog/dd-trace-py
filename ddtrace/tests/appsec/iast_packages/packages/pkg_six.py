from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_six = Blueprint("package_six", __name__)


@pkg_six.route("/six")
def pkg_requests_view():
    import six

    response = ResultResponse(request.args.get("package_param"))

    try:
        if six.PY2:
            text = "We're in Python 2"
        else:
            text = "We're in Python 3"

        response.result1 = text
    except Exception as e:
        response.result1 = str(e)

    return response.json()
