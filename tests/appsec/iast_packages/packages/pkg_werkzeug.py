"""
werkzeug==3.0.3

https://pypi.org/project/werkzeug/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_werkzeug = Blueprint("package_werkzeug", __name__)


@pkg_werkzeug.route("/werkzeug")
def pkg_werkzeug_view():
    from werkzeug.security import check_password_hash
    from werkzeug.security import generate_password_hash

    response = ResultResponse(request.args.get("package_param"))

    try:
        password = request.args.get("package_param", "default-password")

        try:
            hashed_password = generate_password_hash(password)
            password_match = check_password_hash(hashed_password, password)
            result_output = (
                f"Original password: {password}\nHashed password: replaced_hashed\nPassword match: {password_match}"
            )
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
