# 3p
import base64
import json

# project
from ...internal.logger import get_logger
from ...propagation.http import HTTPPropagator

log = get_logger(__name__)
propagator = HTTPPropagator()


def modify_client_context(client_context_base64, trace_headers):
    try:
        client_context_json = base64.b64decode(client_context_base64).decode('utf-8')
        client_context_object = json.loads(client_context_json)

        if 'custom' in client_context_object:
            client_context_object['custom']['_datadog'] = trace_headers
        else:
            client_context_object['custom'] = {
                '_datadog': trace_headers
            }

        new_context = base64.b64encode(json.dumps(client_context_object).encode('utf-8')).decode('utf-8')
        return new_context
    except Exception:
        log.warning('malformed client_context=%s', client_context_base64, exc_info=True)
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
            'custom': {
                '_datadog': trace_headers
            }
        }
        json_context = json.dumps(client_context_object).encode('utf-8')
        params['ClientContext'] = base64.b64encode(json_context).decode('utf-8')
