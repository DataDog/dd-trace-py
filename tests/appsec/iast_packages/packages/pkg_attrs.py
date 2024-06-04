"""
attrs==23.2.0
https://pypi.org/project/attrs/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_attrs = Blueprint("package_attrs", __name__)


@pkg_attrs.route("/attrs")
def pkg_attrs_view():
    import attrs

    response = ResultResponse(request.args.get("package_param"))

    try:

        @attrs.define
        class User:
            name: str
            age: int

        user = User(name=response.package_param, age=65)

        response.result1 = {"name": user.name, "age": user.age}
    except Exception as e:
        response.result1 = str(e)

    return response.json()
