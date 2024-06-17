"""
itsdangerous==2.2.0

https://pypi.org/project/itsdangerous/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_itsdangerous = Blueprint("package_itsdangerous", __name__)


@pkg_itsdangerous.route("/itsdangerous")
def pkg_itsdangerous_view():
    from itsdangerous import BadSignature
    from itsdangerous import Signer

    response = ResultResponse(request.args.get("package_param"))

    try:
        secret_key = "secret-key"
        param_value = request.args.get("package_param", "default-value")

        signer = Signer(secret_key)
        signed_value = signer.sign(param_value)

        try:
            unsigned_value = signer.unsign(signed_value)
            # this changes from run to run, so we generate a fixed value
            signed_decoded = signed_value.decode().split(".")[0] + ".generated_signature"
            result_output = f"Signed value: {signed_decoded}\nUnsigned value: {unsigned_value.decode()}"
        except BadSignature:
            result_output = "Failed to verify the signed value."

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
