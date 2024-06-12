"""
s3transfer==0.10.1

https://pypi.org/project/s3transfer/
"""
from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_s3transfer = Blueprint("package_s3transfer", __name__)

# TODO: this won't actually download since will always fail with NoCredentialsError


@pkg_s3transfer.route("/s3transfer")
def pkg_s3transfer_view():
    import boto3
    from botocore.exceptions import NoCredentialsError
    import s3transfer

    response = ResultResponse(request.args.get("package_param"))

    try:
        s3_client = boto3.client("s3")
        transfer = s3transfer.S3Transfer(s3_client)

        bucket_name = "example-bucket"
        object_key = "example-object"
        file_path = "/path/to/local/file"

        transfer.download_file(bucket_name, object_key, file_path)

        _ = f"File {object_key} downloaded from bucket {bucket_name} to {file_path}"
    except NoCredentialsError:
        _ = "Credentials not available"
    except Exception as e:
        response.result1 = str(e)

    return response.json()
