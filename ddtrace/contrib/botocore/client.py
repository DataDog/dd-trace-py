"""
Trace queries to aws api done via botocore client
"""

# stdlib
import logging

# project

# 3p
import botocore.client


log = logging.getLogger(__name__)

DEFAULT_SERVICE = "aws"


def get_traced_botocore_client(tracer, service=DEFAULT_SERVICE, meta=None):
    return _get_traced_botocore_client(botocore.client, tracer, service, meta)


def _get_traced_botocore_client(cassandra, tracer, service=DEFAULT_SERVICE, meta=None):
    """ Trace botocore commands by patching the BaseClient class """

    tracer.set_service_info(
        service=service,
    )

    class TracedBaseClient(botocore.client.BaseClient):
        _dd_tracer = tracer
        _dd_service = service
        _dd_tags = meta

        def __init__(self, *args, **kwargs):
            super(TracedBaseClient, self).__init__(*args, **kwargs)

        def _make_api_call(self, operation_name, api_params):
            if not self._dd_tracer:
                return super(TracedBaseClient, self)._make_api_call(operation_name, api_params)

            with self._dd_tracer.trace("aws.api.request", service=self._dd_service) as span:
                _endpoint = getattr(self, "_endpoint", None)
                endpoint_name = getattr(_endpoint, "_endpoint_prefix", "unknown")
                _boto_meta = getattr(self, "meta", None)
                region_name = getattr(_boto_meta, "region_name", "unknown")
                meta = {
                    'aws.agent': 'botocore',
                    'aws.operation': operation_name,
                    'aws.endpoint': endpoint_name,
                    'aws.region': region_name,
                }
                add_cleaned_api_params(meta, operation_name, api_params)
                span.resource = '%s.%s.%s' % (operation_name, endpoint_name, region_name)
                span.set_tags(meta)

                return super(TracedBaseClient, self)._make_api_call(operation_name, api_params)


def add_cleaned_api_params(meta, operation_name, api_params):
    """ Avoid sending sensitive information
    """
    if operation_name != 'AssumeRole':
        meta['aws.api_params'] = str(api_params)
