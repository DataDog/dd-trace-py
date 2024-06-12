"""
s3fs==2024.5.0
https://pypi.org/project/s3fs/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_s3fs = Blueprint("package_s3fs", __name__)


@pkg_s3fs.route("/s3fs")
def pkg_s3fs_view():
    import s3fs

    response = ResultResponse(request.args.get("package_param"))

    try:
        fs = s3fs.S3FileSystem(anon=False)
        bucket_name = request.args.get("bucket_name", "your-default-bucket")
        files = fs.ls(bucket_name)

        _ = {"files": files}
    except Exception as e:
        _ = str(e)

    return response.json()
