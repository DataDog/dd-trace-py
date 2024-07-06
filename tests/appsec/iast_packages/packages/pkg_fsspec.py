"""
fsspec==2024.5.0
https://pypi.org/project/fsspec/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_fsspec = Blueprint("package_fsspec", __name__)


@pkg_fsspec.route("/fsspec")
def pkg_fsspec_view():
    import fsspec

    response = ResultResponse(request.args.get("package_param"))

    try:
        fs = fsspec.filesystem("file")
        files = fs.ls(".")

        response.result1 = files[0]
    except Exception as e:
        response.error = str(e)

    return response.json()
