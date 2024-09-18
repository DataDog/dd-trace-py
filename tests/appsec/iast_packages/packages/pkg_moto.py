"""
moto==5.0.11


https://pypi.org/project/moto/
"""

from flask import Blueprint
from flask import request

from .utils import ResultResponse


pkg_moto = Blueprint("package_moto", __name__)


class MyModel:
    def __init__(self, name, value, bucket_name):
        self.name = name
        self.value = value
        self.bucket_name = bucket_name

    def save(self):
        import boto3

        s3 = boto3.client("s3", region_name="us-east-1")
        s3.put_object(Bucket=self.bucket_name, Key=self.name, Body=self.value)


@pkg_moto.route("/moto[s3]")
def pkg_moto_view():
    import boto3
    from moto import mock_aws

    @mock_aws
    def test_my_model_save(bucket_name):
        conn = boto3.resource("s3", region_name="us-east-1")
        # We need to create the bucket since this is all in Moto's 'virtual' AWS account
        conn.create_bucket(Bucket=bucket_name)
        model_instance = MyModel("somename", "right_result", bucket_name)
        model_instance.save()
        body = conn.Object(bucket_name, "somename").get()["Body"].read().decode("utf-8")
        return body

    response = ResultResponse(request.args.get("package_param"))

    try:
        bucket_name = request.args.get("package_param", "some_bucket")

        try:
            result_output = test_my_model_save(bucket_name)
        except Exception as e:
            result_output = f"Error: {str(e)}"

        response.result1 = result_output
    except Exception as e:
        response.result1 = f"Error: {str(e)}"

    return response.json()
