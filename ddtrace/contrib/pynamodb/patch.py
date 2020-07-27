"""
Trace queries to botocore api done via pynamodb client
"""

from ddtrace.vendor import wrapt
from ddtrace import config
import pynamodb.connection.base


from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...pin import Pin
from ...ext import SpanTypes
from ...utils.formats import deep_getattr, get_env
from ...utils.wrappers import unwrap

# Pynamodb connection class
_PynamoDB_client = pynamodb.connection.base.Connection

config._add('pynamodb', {
    'service_name': get_env("pynamodb", "service_name") or 'pynamodb',
})


def patch():

    if getattr(pynamodb.connection.base, '_datadog_patch', False):
        return
    setattr(pynamodb.connection.base, '_datadog_patch', True)

    wrapt.wrap_function_wrapper('pynamodb.connection.base', 'Connection._make_api_call', patched_api_call)
    Pin(service=None).onto(pynamodb.connection.base.Connection)


def unpatch():
    if getattr(pynamodb.connection.base, '_datadog_patch', False):
        setattr(pynamodb.connection.base, '_datadog_patch', False)
        unwrap(pynamodb.connection.base.Connection, '_make_api_call')


def patched_api_call(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = deep_getattr(instance, 'client._endpoint._endpoint_prefix')

    with pin.tracer.trace('pynamodb.command',
                          service=config.pynamodb['service_name'],
                          span_type=SpanTypes.HTTP) as span:

        span.set_tag(SPAN_MEASURED_KEY)

        operation = None
        if args:
            operation = args[0]
            span.resource = '%s.%s' % (endpoint_name, operation.lower())

        else:
            span.resource = endpoint_name

        region_name = deep_getattr(instance, 'client.meta.region_name')

        meta = {
            'aws.agent': 'pynamodb',
            'aws.operation': operation,
            'aws.region': region_name,
        }
        span.set_tags(meta)
        result = original_func(*args, **kwargs)

        # Depending on the query type, there are several ways the TableName can be found
        if 'ConsumedCapacity' in result:
            span.set_tag('TableName', result['ConsumedCapacity']['TableName'])

        if 'Table' in result and 'TableName' in result['Table']:
            span.set_tag('TableName', result['Table']['TableName'])

        if 'TableDescription' in result and 'TableName' in result['TableDescription']:
            span.set_tag('TableName', result['TableDescription']['TableName'])

        # set analytics sample rate
        span.set_tag(
            ANALYTICS_SAMPLE_RATE_KEY,
            config.pynamodb.get_analytics_sample_rate()
        )

        return result
