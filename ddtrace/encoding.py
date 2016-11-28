import json
import logging


# check msgpack CPP implementation; if the import fails, we're using the
# pure Python implementation that is really slow, so the ``Encoder`` should use
# a different encoding format.
try:
    import msgpack
    from msgpack._packer import Packer  # noqa
    from msgpack._unpacker import unpack, unpackb, Unpacker  # noqa
    MSGPACK_ENCODING = True
except ImportError:
    MSGPACK_ENCODING = False

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
        self.content_type = ''

    def encode_traces(self, traces):
        """
        Encodes a list of traces, expecting a list of items where each items
        is a list of spans. Before dump the string in a serialized format all
        traces are normalized, calling the ``to_dict()`` method. The traces
        nesting is not changed.

        :param traces: A list of traces that should be serialized
        """
        normalized_traces = [[span.to_dict() for span in trace] for trace in traces]
        return self._encode(normalized_traces)

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
        # TODO[manu]: add instructions about how users can switch to Msgpack
        log.debug('using JSON encoder; application performance may be degraded')
        self.content_type = 'application/json'

    def _encode(self, obj):
        return json.dumps(obj)


class MsgpackEncoder(Encoder):
    def __init__(self):
        log.debug('using Msgpack encoder')
        self.content_type = 'application/msgpack'

    def _encode(self, obj):
        return msgpack.packb(obj, use_bin_type=True)


def get_encoder():
    """
    Switching logic that choose the best encoder for the API transport.
    The default behavior is to use Msgpack if we have a CPP implementation
    installed, falling back to the Python built-in JSON encoder.
    """
    if MSGPACK_ENCODING:
        return MsgpackEncoder()
    else:
        return JSONEncoder()
