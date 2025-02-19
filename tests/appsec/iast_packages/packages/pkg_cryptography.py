"""
cryptography==42.0.7
https://pypi.org/project/cryptography/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_cryptography = Blueprint("package_cryptography", __name__)


@pkg_cryptography.route("/cryptography")
def pkg_cryptography_view():
    from cryptography.fernet import Fernet

    response = ResultResponse(request.args.get("package_param"))

    try:
        key = Fernet.generate_key()
        fernet = Fernet(key)

        encrypted_message = fernet.encrypt(response.package_param.encode())
        decrypted_message = fernet.decrypt(encrypted_message).decode()

        result = {
            "key": key.decode(),
            "encrypted_message": encrypted_message.decode(),
            "decrypted_message": decrypted_message,
        }

        response.result1 = result["decrypted_message"]
    except Exception as e:
        response.result1 = str(e)

    return response.json()


@pkg_cryptography.route("/cryptography_propagation")
def pkg_cryptography_propagation_view():
    from cryptography.fernet import Fernet

    from ddtrace.appsec._iast._taint_tracking import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))
    if not is_pyobject_tainted(response.package_param):
        response.result1 = "Error: package_param is not tainted"
        return response.json()

    try:
        key = Fernet.generate_key()
        fernet = Fernet(key)

        encrypted_message = fernet.encrypt(response.package_param.encode())
        decrypted_message = fernet.decrypt(encrypted_message).decode()

        result = {
            "key": key.decode(),
            "encrypted_message": encrypted_message.decode(),
            "decrypted_message": decrypted_message,
        }

        if not is_pyobject_tainted(result["decrypted_message"]):
            response.result1 = "Error: result['decrypted_message'] is not tainted: %s" % result["decrypted_message"]
            return response.json()

        response.result1 = "OK"
    except Exception as e:
        response.result1 = str(e)

    return response.json()
