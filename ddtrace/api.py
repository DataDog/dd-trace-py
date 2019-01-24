# stdlib
import json
import logging
import time

import ddtrace

# project
from .encoding import get_encoder, JSONEncoder
from .compat import httplib, PYTHON_VERSION, PYTHON_INTERPRETER, get_connection_response


log = logging.getLogger(__name__)

TRACE_COUNT_HEADER = 'X-Datadog-Trace-Count'

_VERSIONS = {'v0.4': {'traces': '/v0.4/traces',
                      'services': '/v0.4/services',
                      'compatibility_mode': False,
                      'fallback': 'v0.3'},
             'v0.3': {'traces': '/v0.3/traces',
                      'services': '/v0.3/services',
                      'compatibility_mode': False,
                      'fallback': 'v0.2'},
             'v0.2': {'traces': '/v0.2/traces',
                      'services': '/v0.2/services',
                      'compatibility_mode': True,
                      'fallback': None}}


def _parse_response_json(response):
    """
    Parse the content of a response object, and return the right type,
    can be a string if the output was plain text, or a dictionnary if
    the output was a JSON.
    """
    if hasattr(response, 'read'):
        body = response.read()
        try:
            if not isinstance(body, str) and hasattr(body, 'decode'):
                body = body.decode('utf-8')

            if not body:
                log.debug('Empty reply from trace-agent status:%d', getattr(response, 'status', None))
                return
            elif hasattr(body, 'startswith') and body.startswith('OK'):
                # This typically happens when using a priority-sampling enabled
                # library with an outdated agent. It still works, but priority sampling
                # will probably send too many traces, so the next step is to upgrade agent.
                log.debug('"OK" is not a valid JSON, please make sure trace-agent is up to date')
                return
            return json.loads(body)
        except (ValueError, TypeError) as err:
            log.debug('Unable to load JSON %r: %s', body, err)


class API(object):
    """
    Send data to the trace agent using the HTTP protocol and JSON format
    """
    def __init__(self, hostname, port, headers=None, encoder=None, priority_sampling=False):
        self.hostname = hostname
        self.port = port

        self._headers = headers or {}
        self._version = None

        if priority_sampling:
            self._set_version('v0.4', encoder=encoder)
        else:
            self._set_version('v0.3', encoder=encoder)

        self._headers.update({
            'Datadog-Meta-Lang': 'python',
            'Datadog-Meta-Lang-Version': PYTHON_VERSION,
            'Datadog-Meta-Lang-Interpreter': PYTHON_INTERPRETER,
            'Datadog-Meta-Tracer-Version': ddtrace.__version__,
        })

    def _set_version(self, version, encoder=None):
        if version not in _VERSIONS:
            version = 'v0.2'
        if version == self._version:
            return
        self._version = version
        self._traces = _VERSIONS[version]['traces']
        self._services = _VERSIONS[version]['services']
        self._fallback = _VERSIONS[version]['fallback']
        self._compatibility_mode = _VERSIONS[version]['compatibility_mode']
        if self._compatibility_mode:
            self._encoder = JSONEncoder()
        else:
            self._encoder = encoder or get_encoder()
        # overwrite the Content-type with the one chosen in the Encoder
        self._headers.update({'Content-Type': self._encoder.content_type})

    def _downgrade(self, payload):
        """
        Downgrades the used encoder and API level. This method must fallback to a safe
        encoder and API, so that it will success despite users' configurations. This action
        ensures that the compatibility mode is activated so that the downgrade will be
        executed only once.
        """
        self._set_version(self._fallback)

    def send_traces(self, payload):
        if not payload or payload.empty:
            return

        start = time.time()
        data = payload.get_payload()
        response = self._put(self._traces, data, payload.length)

        # the API endpoint is not available so we should downgrade the connection and re-try the call
        if response.status in [404, 415] and self._fallback:
            log.debug('Calling endpoint "%s" but received %s; downgrading API', self._traces, response.status)
            self._downgrade()
            payload.downgrade(self._encoder)
            return self.send_traces(payload)

        log.debug('Reported %d traces in %.5fs', payload.length, time.time() - start)
        return response

    def _put(self, endpoint, data, count=0):
        conn = httplib.HTTPConnection(self.hostname, self.port)
        try:
            headers = self._headers
            if count:
                headers = dict(self._headers)
                headers[TRACE_COUNT_HEADER] = str(count)

            log.debug('PUT %s:%s%s with payload %sb', self.hostname, self.port, endpoint, len(data))
            conn.request('PUT', endpoint, data, headers)
            resp = get_connection_response(conn)
            log.debug(
                'Response %d %s length:%d',
                getattr(resp, 'status', None),
                getattr(resp, 'reason', None),
                getattr(resp, 'length', None),
            )
            return resp
        finally:
            conn.close()
