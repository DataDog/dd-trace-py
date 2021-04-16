from contextlib import contextmanager
import io
import zipfile

import aiobotocore.session

from ddtrace import Pin


LOCALSTACK_ENDPOINT_URL = {
    "s3": "http://localhost:5000",
    "ec2": "http://localhost:5001",
    "kms": "http://localhost:5002",
    "sqs": "http://localhost:5003",
    "lambda": "http://localhost:5004",
    "kinesis": "http://localhost:5005",
}


@contextmanager
def aiobotocore_client(service, tracer):
    """Helper function that creates a new aiobotocore client so that
    it is closed at the end of the context manager.
    """
    session = aiobotocore.session.get_session()
    endpoint = LOCALSTACK_ENDPOINT_URL[service]
    client = session.create_client(
        service,
        region_name="us-west-2",
        endpoint_url=endpoint,
        aws_access_key_id="aws",
        aws_secret_access_key="aws",
        aws_session_token="aws",
    )
    Pin.override(client, tracer=tracer)
    try:
        yield client
    finally:
        client.close()


def get_zip_lambda():
    """Helper function that returns a valid lambda package."""
    code = """
def lambda_handler(event, context):
    return event
"""
    zip_output = io.BytesIO()
    zip_file = zipfile.ZipFile(zip_output, "w", zipfile.ZIP_DEFLATED)
    zip_file.writestr("lambda_function.py", code)
    zip_file.close()
    zip_output.seek(0)
    return zip_output.read()
