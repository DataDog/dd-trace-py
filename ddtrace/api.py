import json
import socket

from .compat import httplib
from .internal.logger import get_logger


log = get_logger(__name__)


class Response(object):
    """
    Custom API Response object to represent a response from calling the API.

    We do this to ensure we know expected properties will exist, and so we
    can call `resp.read()` and load the body once into an instance before we
    close the HTTPConnection used for the request.
    """
    __slots__ = ['status', 'body', 'reason', 'msg']

    def __init__(self, status=None, body=None, reason=None, msg=None):
        self.status = status
        self.body = body
        self.reason = reason
        self.msg = msg

    @classmethod
    def from_http_response(cls, resp):
        """
        Build a ``Response`` from the provided ``HTTPResponse`` object.

        This function will call `.read()` to consume the body of the ``HTTPResponse`` object.

        :param resp: ``HTTPResponse`` object to build the ``Response`` from
        :type resp: ``HTTPResponse``
        :rtype: ``Response``
        :returns: A new ``Response``
        """
        return cls(
            status=resp.status,
            body=resp.read(),
            reason=getattr(resp, 'reason', None),
            msg=getattr(resp, 'msg', None),
        )

    def get_json(self):
        """Helper to parse the body of this request as JSON"""
        try:
            body = self.body
            if not body:
                log.debug('Empty reply from Datadog Agent, %r', self)
                return

            if not isinstance(body, str) and hasattr(body, 'decode'):
                body = body.decode('utf-8')

            if hasattr(body, 'startswith') and body.startswith('OK'):
                # This typically happens when using a priority-sampling enabled
                # library with an outdated agent. It still works, but priority sampling
                # will probably send too many traces, so the next step is to upgrade agent.
                log.debug('Cannot parse Datadog Agent response, please make sure your Datadog Agent is up to date')
                return

            return json.loads(body)
        except (ValueError, TypeError):
            log.debug('Unable to parse Datadog Agent JSON response: %r', body, exc_info=True)

    def __repr__(self):
        return '{0}(status={1!r}, body={2!r}, reason={3!r}, msg={4!r})'.format(
            self.__class__.__name__,
            self.status,
            self.body,
            self.reason,
            self.msg,
        )


class UDSHTTPConnection(httplib.HTTPConnection):
    """An HTTP connection established over a Unix Domain Socket."""

    # It's "important" to keep the hostname and port arguments here; while there are not used by the connection
    # mechanism, they are actually used as HTTP headers such as `Host`.
    def __init__(self, path, https, *args, **kwargs):
        if https:
            httplib.HTTPSConnection.__init__(self, *args, **kwargs)
        else:
            httplib.HTTPConnection.__init__(self, *args, **kwargs)
        self.path = path

    def connect(self):
        sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        sock.connect(self.path)
        self.sock = sock
