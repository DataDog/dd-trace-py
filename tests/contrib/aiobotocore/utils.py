import aiobotocore.session
from async_generator import async_generator
from async_generator import asynccontextmanager
from async_generator import yield_

from ddtrace.trace import Pin
from tests.contrib.config import MOTO_CONFIG


@asynccontextmanager
@async_generator
async def aiobotocore_client(service, tracer):
    """Helper function that creates a new aiobotocore client so that
    it is closed at the end of the context manager.
    """
    session = aiobotocore.session.get_session()
    endpoint = "http://{host}:{port}".format(**MOTO_CONFIG)
    client = session.create_client(
        service,
        region_name="us-west-2",
        endpoint_url=endpoint,
        aws_access_key_id="aws",
        aws_secret_access_key="aws",
        aws_session_token="aws",
    )

    """Check that ClientCreatorContext exists and that client is an expected type before async with
    ClientCreatorContext was added in aiobotocore 1.x: https://github.com/aio-libs/aiobotocore/pull/659
    In 0.x, client evaluates to aiobotocore.client.EC2 while in 1.x, client
    evaluates to aiobotocore.session.ClientCreatorContext
    """
    if hasattr(aiobotocore.session, "ClientCreatorContext") and isinstance(
        client, aiobotocore.session.ClientCreatorContext
    ):
        async with client as client:
            Pin.override(client, tracer=tracer)
            await yield_(client)

    else:
        Pin.override(client, tracer=tracer)
        try:
            await yield_(client)
        finally:
            await client.close()
