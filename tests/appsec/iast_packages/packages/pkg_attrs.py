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


@pkg_attrs.route("/attrs_propagation")
def pkg_attrs_propagation_view():
    import attrs

    from ddtrace.appsec._iast._taint_tracking._taint_objects_base import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:

        @attrs.define
        class UserPropagation:
            name: str
            age: int

        user = UserPropagation(name=response.package_param, age=65)
        if not is_pyobject_tainted(user.name):
            response.result1 = "Error: user.name is not tainted"
            return response.json()

        response.result1 = "OK"
    except Exception as e:
        response.result1 = str(e)

    return response.json()
