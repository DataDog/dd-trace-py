# 3p
import json


# project
from ...propagation.http import HTTPPropagator


propagator = HTTPPropagator()


def inject_trace_to_message_metadata(args, span):
    trace_headers = {}
    propagator.inject(span.context, trace_headers)
    params = args[1]

    if 'MessageAttributes' not in params:
        params['MessageAttributes'] = {}
    if len(params['MessageAttributes']) < 10:
        params['MessageAttributes']['_datadog'] = {
            'DataType': 'String',
            'StringValue': json.dumps(trace_headers)
        }
