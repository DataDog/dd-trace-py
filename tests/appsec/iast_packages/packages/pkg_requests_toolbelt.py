"""
requests-toolbelt==1.0.0

https://pypi.org/project/requests-toolbelt/
"""
from flask import Blueprint
from flask import jsonify
from flask import request

from .utils import ResultResponse


pkg_requests_toolbelt = Blueprint("package_requests_toolbelt", __name__)


@pkg_requests_toolbelt.route("/requests-toolbelt")
def pkg_requests_toolbelt_view():
    import requests
    from requests_toolbelt import MultipartEncoder

    response = ResultResponse(request.args.get("package_param"))

    try:
        param_value = request.args.get("package_param", "default_value")

        try:
            # Use MultipartEncoder to create multipart form data
            m = MultipartEncoder(fields={"field1": "value1", "field2": param_value})
            url = "https://httpbin.org/post"
            response = requests.post(url, data=m, headers={"Content-Type": m.content_type})
            result_output = response.text
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output.replace("\n", "\\n").replace('"', '\\"').replace("'", "\\'")
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return jsonify(response.json())
