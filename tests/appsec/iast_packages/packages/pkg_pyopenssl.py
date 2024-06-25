"""
pyOpenSSL==23.0.0

https://pypi.org/project/pyOpenSSL/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_pyopenssl = Blueprint("package_pyopenssl", __name__)


@pkg_pyopenssl.route("/pyopenssl")
def pkg_pyopenssl_view():
    from OpenSSL import crypto

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "example.com")

        try:
            # Generate a key pair
            key = crypto.PKey()
            key.generate_key(crypto.TYPE_RSA, 2048)

            # Create a self-signed certificate
            cert = crypto.X509()
            cert.get_subject().CN = param_value
            cert.set_serial_number(1000)
            cert.gmtime_adj_notBefore(0)
            cert.gmtime_adj_notAfter(10 * 365 * 24 * 60 * 60)
            cert.set_issuer(cert.get_subject())
            cert.set_pubkey(key)
            cert.sign(key, "sha256")

            # Convert the certificate to a string
            cert_str = crypto.dump_certificate(crypto.FILETYPE_PEM, cert).decode("utf-8")
            key_str = crypto.dump_privatekey(crypto.FILETYPE_PEM, key).decode("utf-8")

            if cert_str and key_str:
                result_output = "Certificate: replaced_cert; Private Key: replaced_priv_key"
            else:
                result_output = "Error: Unable to generate certificate and private key."
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output.replace("\n", "\\n").replace('"', '\\"').replace("'", "\\'")
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
