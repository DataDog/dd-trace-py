"""
rsa==4.9

https://pypi.org/project/rsa/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_rsa = Blueprint("package_rsa", __name__)


@pkg_rsa.route("/rsa")
def pkg_rsa_view():
    import rsa

    response = ResultResponse(request.args.get("package_param"))

    try:
        (public_key, private_key) = rsa.newkeys(512)

        message = response.package_param
        encrypted_message = rsa.encrypt(message.encode(), public_key)
        decrypted_message = rsa.decrypt(encrypted_message, private_key).decode()
        _ = (encrypted_message.hex(),)
        response.result1 = {"message": message, "decrypted_message": decrypted_message}
    except Exception as e:
        response.result1 = str(e)

    return response.json()


@pkg_rsa.route("/rsa_propagation")
def pkg_rsa_propagation_view():
    import rsa

    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        (public_key, private_key) = rsa.newkeys(512)

        message = response.package_param
        encrypted_message = rsa.encrypt(message.encode(), public_key)
        decrypted_message = rsa.decrypt(encrypted_message, private_key).decode()
        response.result1 = (
            "OK"
            if is_pyobject_tainted(decrypted_message)
            else "Error: decrypted_message is not tainted: %s" % decrypted_message
        )
    except Exception as e:
        response.result1 = str(e)

    return response.json()
