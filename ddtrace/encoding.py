import json
import msgpack
import logging

from .compat import MSGPACK_CPP
from .util import flatten_spans


log = logging.getLogger(__name__)


class Encoder(object):
    """
    Encoder interface that provides the logic to encode traces and service.
    """
    def __init__(self):
        """
        When extending the ``Encoder`` class, ``headers`` must be set because
        they're returned by the encoding methods, so that the API transport doesn't
        need to know what is the right header to suggest the decoding format to the
        agent
        """
        self.headers = {}

    def encode_traces(self, traces):
        """
        Encodes a list of traces, expecting a list of items where each items
        is a list of spans. Before dump the string in a serialized format, the list
        is flatten.

        :param traces: A list of traces that should be serialized
        """
        spans = flatten_spans(traces)
        return self._encode(spans)

    def encode_services(self, services):
        """
        Encodes a dictionary of services.

        :param services: A dictionary that contains one or more services
        """
        return self._encode(services)

    def _encode(self, obj):
        """
        Defines the underlying format used during traces or services encoding.
        This method must be implemented and should only be used by the internal functions.
        """
        raise NotImplementedError


class JSONEncoder(Encoder):
    def __init__(self):
        self.headers = { 'Content-Type': 'application/json' }

    def _encode(self, obj):
        log.debug('using JSON encoder; application performance may be degraded')
        return json.dumps(obj)


class MsgpackEncoder(Encoder):
    def __init__(self):
        self.headers = { 'Content-Type': 'application/msgpack' }

    def _encode(self, obj):
        log.debug('using Msgpack encoder')
        return msgpack.packb(obj, use_bin_type=True)


def get_encoder():
    """
    Switching logic that choose the best encoder for the API transport.
    The default behavior is to use Msgpack if we have a CPP implementation
    installed, falling back to the Python built-in JSON encoder.
    """
    if MSGPACK_CPP:
        return MsgpackEncoder()
    else:
        return JSONEncoder()
