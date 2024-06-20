"""
PyJWT==2.8.0

https://pypi.org/project/PyJWT/
"""
import datetime

from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_pyjwt = Blueprint("package_pyjwt", __name__)


@pkg_pyjwt.route("/pyjwt")
def pkg_pyjwt_view():
    import jwt

    response = ResultResponse(request.args.get("package_param"))

    try:
        secret_key = "your-256-bit-secret"
        user_payload = request.args.get("package_param", "default-user")

        payload = {"user": user_payload, "exp": datetime.datetime.utcnow() + datetime.timedelta(seconds=30)}

        try:
            # Encode the payload to create a JWT
            token = jwt.encode(payload, secret_key, algorithm="HS256")
            result_output = "Encoded JWT: replaced_token"

            # Decode the JWT to verify and read the payload
            decoded_payload = jwt.decode(token, secret_key, algorithms=["HS256"])
            del decoded_payload["exp"]
            result_output += f"\nDecoded payload: {decoded_payload}"
        except jwt.ExpiredSignatureError:
            result_output = "Token has expired"
        except jwt.InvalidTokenError:
            result_output = "Invalid token"
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
