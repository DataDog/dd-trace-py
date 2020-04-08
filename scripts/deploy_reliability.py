import boto3
import os
import tempfile

S3_BUCKET_NAME = "datadog-reliability-env"
client = boto3.client(
    "s3",
    aws_access_key_id=os.getenv("RELIABILITY_AWS_ACCESS_KEY_ID"),
    aws_secret_access_key=os.getenv("RELIABILITY_AWS_SECRET_ACCESS_KEY"),
)
transfer = boto3.s3.transfer.S3Transfer(client)

with tempfile.NamedTemporaryFile(mode="w") as fp:
    for line in [
        os.getenv("CIRCLE_BRANCH"),
        os.getenv("CIRCLE_SHA1"),
        os.getenv("DDTRACE_VERSION"),
        os.getenv("CIRCLE_USERNAME"),
    ]:
        fp.write(f"{line}\n")

    filename = "master" if os.getenv("CIRCLE_BRANCH") == "master" else "dev"
    transfer.upload_file(fp.name, S3_BUCKET_NAME, f"python/{filename}.txt")
