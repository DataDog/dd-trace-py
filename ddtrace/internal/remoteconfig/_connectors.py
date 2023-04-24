import abc
from ctypes import c_char
import json
from uuid import UUID

import six

from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import to_unicode


# Size of the shared variable. It's calculated based on Remote Config Payloads
SHARED_MEMORY_SIZE = 603432


class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            # if the obj is uuid, we simply return the value of uuid
            return obj.hex
        return json.JSONEncoder.default(self, obj)


class ConnectorBase(six.with_metaclass(abc.ABCMeta)):
    """Connector is the bridge between Publisher and Subscriber class"""

    @abc.abstractmethod
    def read(self):
        pass

    @abc.abstractmethod
    def write(self, metadata, config_raw):
        pass


class ConnectorSharedMemoryJson(ConnectorBase):
    """ConnectorSharedMemoryJson uses an array of chars to share information between processes.
    `multiprocessing.Array``, as far as we know, was the most efficient way to share information. We compare this
    approach with: Multiprocess Manager, Multiprocess Value, Multiprocess Queues
    """

    def __init__(self):
        import multiprocessing

        self.data = multiprocessing.Array(c_char, SHARED_MEMORY_SIZE, lock=False)

    def write(self, metadata, config_raw):
        if PY2:
            data = bytes(json.dumps(config_raw))
        else:
            data = bytes(json.dumps(config_raw), encoding="utf-8")

        self.data.value = data

    def read(self):
        config = {}
        config_raw = to_unicode(self.data.value)
        if config_raw:
            config = json.loads(config_raw)
        return config


class ConnectorSharedMemoryMetadataJson(ConnectorBase):
    def __init__(self):
        import multiprocessing

        self.data = multiprocessing.Array(c_char, SHARED_MEMORY_SIZE, lock=False)

    def write(self, metadata, config_raw):
        if PY2:
            data = bytes(json.dumps({"metadata": metadata, "config": config_raw}, cls=UUIDEncoder))
        else:
            data = bytes(json.dumps({"metadata": metadata, "config": config_raw}, cls=UUIDEncoder), encoding="utf-8")

        self.data.value = data

    def read(self):
        config = {}
        config_raw = to_unicode(self.data.value)
        if config_raw:
            config = json.loads(config_raw)
        return config
