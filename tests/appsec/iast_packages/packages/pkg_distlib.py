"""
distlib==0.3.8

https://pypi.org/project/distlib/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_distlib = Blueprint("package_distlib", __name__)


@pkg_distlib.route("/distlib")
def pkg_distlib_view():
    import distlib.metadata

    response = ResultResponse(request.args.get("package_param"))

    try:
        metadata = distlib.metadata.Metadata()
        metadata.name = "example-package"
        metadata.version = "0.1"

        # Format the parsed metadata info into a string
        result_output = f"Name: {metadata.name}\nVersion: {metadata.version}\n"
        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"
    return response.json()
