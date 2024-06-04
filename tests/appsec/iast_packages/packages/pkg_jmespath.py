"""
jmespath==1.0.1
https://pypi.org/project/jmespath/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_jmespath = Blueprint("package_jmespath", __name__)


@pkg_jmespath.route("/jmespath")
def pkg_jmespath_view():
    import jmespath

    response = ResultResponse(request.args.get("package_param"))

    try:
        data = {
            "locations": [
                {"name": "Seattle", "state": "WA"},
                {"name": "New York", "state": "NY"},
                {"name": "San Francisco", "state": "CA"},
            ]
        }
        expression = jmespath.compile("locations[?state == 'WA'].name | [0]")
        result = expression.search(data)

        response.result1 = result
    except Exception as e:
        response.result1 = str(e)

    return response.json()
