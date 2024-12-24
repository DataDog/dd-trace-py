"""
PyNaCl==1.5.0

https://pypi.org/project/PyNaCl/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_pynacl = Blueprint("package_pynacl", __name__)


@pkg_pynacl.route("/pynacl")
def pkg_pynacl_view():
    from nacl import secret
    from nacl import utils

    response = ResultResponse(request.args.get("package_param"))

    try:
        message = request.args.get("package_param", "Hello, World!").encode("utf-8")

        try:
            # Generate a random key
            key = utils.random(secret.SecretBox.KEY_SIZE)
            box = secret.SecretBox(key)

            # Encrypt the message
            encrypted = box.encrypt(message)
            _ = encrypted.hex()

            # Decrypt the message
            decrypted = box.decrypt(encrypted)
            decrypted_message = decrypted.decode("utf-8")
            _ = key.hex()

            result_output = f"Key: replaced_key; Encrypted: replaced_encrypted; Decrypted: {decrypted_message}"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output.replace("\n", "\\n").replace('"', '\\"').replace("'", "\\'")
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())


@pkg_pynacl.route("/pynacl_propagation")
def pkg_pynacl_propagation_view():
    from nacl import secret
    from nacl import utils

    from ddtrace.appsec._iast._taint_tracking._taint_objects import is_pyobject_tainted

    response = ResultResponse(request.args.get("package_param"))

    try:
        message = request.args.get("package_param", "Hello, World!").encode("utf-8")
        if not is_pyobject_tainted(message):
            response.result1 = "Error: package_param is not tainted"
            return jsonify(response.json())

        try:
            # Generate a random key
            key = utils.random(secret.SecretBox.KEY_SIZE)
            box = secret.SecretBox(key)

            # Encrypt the message
            encrypted = box.encrypt(message)
            _ = encrypted.hex()

            # Decrypt the message
            decrypted = box.decrypt(encrypted)
            decrypted_message = decrypted.decode("utf-8")
            _ = key.hex()

            result_output = (
                "OK"
                if is_pyobject_tainted(decrypted_message)
                else f"Error: decrypted_message is not tainted: {decrypted_message}"
            )
        except Exception as e:
            result_output = f"Error: {str(e)}"
    except Exception as e:
        result_output = f"Error: {str(e)}"

    response.result1 = result_output

    return jsonify(response.json())
