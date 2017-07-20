import aiobotocore.session

from ddtrace import Pin
from contextlib import contextmanager


LOCALSTACK_ENDPOINT_URL = {
    's3': 'http://127.0.0.1:55000',
    'ec2': 'http://127.0.0.1:55001',
    'kms': 'http://127.0.0.1:55002',
    'sqs': 'http://127.0.0.1:55003',
    'lambda': 'http://127.0.0.1:55004',
    'kinesis': 'http://127.0.0.1:55005',
}


@contextmanager
def aiobotocore_client(service, tracer):
    """Helper function that creates a new aiobotocore client so that
    it is closed at the end of the context manager.
    """
    session = aiobotocore.session.get_session()
    endpoint = LOCALSTACK_ENDPOINT_URL[service]
    client = session.create_client(service, region_name='us-west-2', endpoint_url=endpoint)
    Pin.override(client, tracer=tracer)
    try:
        yield client
    finally:
        client.close()
