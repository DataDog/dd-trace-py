"""
Trace queries to aws api done via botocore client
"""
# 3p
from ddtrace.vendor import wrapt
from ddtrace import config
import botocore.client
import base64
import json

# project
from ...constants import ANALYTICS_SAMPLE_RATE_KEY, SPAN_MEASURED_KEY
from ...ext import SpanTypes, http, aws
from ...internal.logger import get_logger
from ...pin import Pin
from ...propagation.http import HTTPPropagator
from ...utils.formats import deep_getattr
from ...utils.wrappers import unwrap

# Original botocore client class
_Botocore_client = botocore.client.BaseClient

ARGS_NAME = ('action', 'params', 'path', 'verb')
TRACED_ARGS = ['params', 'path', 'verb']

propagator = HTTPPropagator()
log = get_logger(__name__)


def modify_client_context(client_context_base64, trace_headers):
    try 
        client_context_json = base64.b64decode(client_context_base64).decode('utf-8')
        client_context_object = json.loads(client_context_json)

        if 'Custom' in client_context_object:
            client_context_object['Custom']['_datadog'] = trace_headers
        else:
            client_context_object['Custom'] = {
                '_datadog': trace_headers
            }

        new_context = base64.b64encode(json.dumps(client_context_object).encode('utf-8')).decode('utf-8')
        return new_context
    except Exception as e:
        log.warning('malformed client_context=%s', client_context_base64)
        return client_context_base64



def inject_trace_to_client_context(args, span):
    trace_headers = {}
    propagator.inject(span.context, trace_headers)

    params = args[1]
    if 'ClientContext' in params:
        params['ClientContext'] = modify_client_context(params['ClientContext'], trace_headers)
    else:
        trace_headers = {}
        propagator.inject(span.context, trace_headers)
        client_context_object = {
            'Custom': {
                '_datadog': trace_headers
            }
        }
        json_context = json.dumps(client_context_object).encode('utf-8')
        params['ClientContext'] = base64.b64encode(json_context).decode('utf-8')


def patch():
    if getattr(botocore.client, '_datadog_patch', False):
        return
    setattr(botocore.client, '_datadog_patch', True)

    wrapt.wrap_function_wrapper('botocore.client', 'BaseClient._make_api_call', patched_api_call)
    Pin(service='aws', app='aws').onto(botocore.client.BaseClient)


def unpatch():
    if getattr(botocore.client, '_datadog_patch', False):
        setattr(botocore.client, '_datadog_patch', False)
        unwrap(botocore.client.BaseClient, '_make_api_call')


def patched_api_call(original_func, instance, args, kwargs):

    pin = Pin.get_from(instance)
    if not pin or not pin.enabled():
        return original_func(*args, **kwargs)

    endpoint_name = deep_getattr(instance, '_endpoint._endpoint_prefix')

    with pin.tracer.trace('{}.command'.format(endpoint_name),
                          service='{}.{}'.format(pin.service, endpoint_name),
                          span_type=SpanTypes.HTTP) as span:
        span.set_tag(SPAN_MEASURED_KEY)
        operation = None
        if args:
            operation = args[0]
            span.resource = '%s.%s' % (endpoint_name, operation.lower())

            if endpoint_name == 'lambda' and operation == 'Invoke':
                inject_trace_to_client_context(args, span)

        else:
            span.resource = endpoint_name

        aws.add_span_arg_tags(span, endpoint_name, args, ARGS_NAME, TRACED_ARGS)

        region_name = deep_getattr(instance, 'meta.region_name')

        meta = {
            'aws.agent': 'botocore',
            'aws.operation': operation,
            'aws.region': region_name,
        }
        span.set_tags(meta)

        result = original_func(*args, **kwargs)

        response_meta = result['ResponseMetadata']

        if 'HTTPStatusCode' in response_meta:
            span.set_tag(http.STATUS_CODE, response_meta['HTTPStatusCode'])

        if 'RetryAttempts' in response_meta:
            span.set_tag('retry_attempts', response_meta['RetryAttempts'])

        if 'RequestId' in response_meta:
            span.set_tag('aws.requestid', response_meta['RequestId'])

        # set analytics sample rate
        span.set_tag(
            ANALYTICS_SAMPLE_RATE_KEY,
            config.botocore.get_analytics_sample_rate()
        )

        return result
