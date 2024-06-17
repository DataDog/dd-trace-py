"""
asn1crypto==1.5.1

https://pypi.org/project/asn1crypto/
"""
from flask import Blueprint
from flask import Flask
from flask import request

from .utils import ResultResponse


app = Flask(__name__)

pkg_asn1crypto = Blueprint("package_asn1crypto", __name__)


@pkg_asn1crypto.route("/asn1crypto")
def pkg_asn1crypto_view():
    from asn1crypto import pem
    from asn1crypto import x509

    response = ResultResponse(request.args.get("package_param"))
    pem_data = """
-----BEGIN CERTIFICATE-----
MIIDXTCCAkWgAwIBAgIJAO0mEjRixKQYMA0GCSqGSIb3DQEBCwUAMEUxCzAJBgNV
BAYTAkNOMQswCQYDVQQIDAJCTjELMAkGA1UEBwwCQk4xCzAJBgNVBAoMAkJOMQsw
CQYDVQQLDAJCTjELMAkGA1UEAwwCQk4wHhcNMTkwNDA0MTIwNjAwWhcNMjkwNDAx
MTIwNjAwWjBFMQswCQYDVQQGEwJDTjELMAkGA1UECAwCQk4xCzAJBgNVBAcMAkJO
MQswCQYDVQQKDAJCTjELMAkGA1UECwwCQk4xCzAJBgNVBAMMAkJOMIIBIjANBgkq
hkiG9w0BAQEFAAOCAQ8AMIIBCgKCAQEApvPexYvKLC9Ru+eC1LFAuj5J9VYnhJ3z
5aM9O8wU6DhvhBfGZcbJcmHqgVp3iwq1H9Y8YWovDz8rps3Ld6EGnffNm2UlI7GG
l+vH/jXzAYkpFP9yGQfw+df4u+nz4lUQwDXYKecAXsM3ZbwB2O6CfyJ5HEPi/9gh
PYB+xbSrxk6jBaBlnskJz2LBwMd1E5eyxwqRu1D3x+0ZrxjKLlmH0OYfx8A+/1Sm
+eb+d8Kq8eT0ZjjNsxAhhNkWth4Vu1bYO3I6f/+o5CHQf8R7sTFwKNRlyXKn3M74
9akE5XIsXz1EvE/57EEQpVZm57U/7/h+lJlzunA2U7EQiMIkFsdB1DRSlwIDAQAB
o1AwTjAdBgNVHQ4EFgQUrK5D1tvb4F/JTQkv7UauT3MOgX4wHwYDVR0jBBgwFoAU
rK5D1tvb4F/JTQkv7UauT3MOgX4wDAYDVR0TBAUwAwEB/zANBgkqhkiG9w0BAQsF
AAOCAQEAxU8iDQ+T/x+d0AtW4HGBH8HfFhIBuH+2mSC2N5mO4xkOKoCZRJhSvqG6
67k5zjE8PzBdl6gXy1F5f7GySMe/xGAANRVFQmHzApQ9ciEkRhLsfgfhB1eRpl5i
v/9EliY6PYNUOi+UllzR+P8Ub3EXB51XkOUC5Izt+G+mdIEm9q7HT1w/Ni98Jgct
oFZ1h8lH7Udv3p2OgkgncQZ6b7kBkhUn5D5d6J1rdMJFS7QNRG5c0XUk7B1jDsR0
2fVfB+Xa6kDJbJOs/xDB6cdFh5QlP9/L5k8g2lNiMZ1SuUuQyb4+JfY69lFYAKZP
x7OiEdDcnpEVlZQ/Nhrb+r1lCJZPm4==
-----END CERTIFICATE-----
""".strip()

    try:
        if pem.detect(pem_data.encode()):
            _, _, der_bytes = pem.unarmor(pem_data.encode())
            _ = x509.Certificate.load(der_bytes)
            response.result1 = "Ok"
        else:
            response.result1 = "Invalid PEM data"
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
