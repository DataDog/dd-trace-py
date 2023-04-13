import abc
from ctypes import c_char
import json
import multiprocessing
import os

import six

from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import to_unicode


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


class ConnectorBase(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def read(self):
        pass

    @abc.abstractmethod
    def write(self, metadata, config_raw):
        pass


class ConnectorFile(ConnectorBase):
    def __init__(self, filename="shared_data.json"):
        self._target_file = os.path.join(ROOT_DIR, filename)

    def write(self, metadata, config_raw):
        fl = open(self._target_file, "w")
        fl.write(json.dumps(config_raw))
        fl.close()

    def read(self):
        fl = open(self._target_file)
        config = fl.read()
        fl.close()
        return json.loads(config)


class ConnectorSharedMemoryJson(ConnectorBase):
    def __init__(self):
        self.data = multiprocessing.Array(c_char, 603432, lock=False)

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
        self.data = multiprocessing.Array(c_char, 603432, lock=False)

    def write(self, metadata, config_raw):
        if PY2:
            data = bytes(json.dumps({"metadata": metadata, "config": config_raw}))
        else:
            data = bytes(json.dumps({"metadata": metadata, "config": config_raw}), encoding="utf-8")

        self.data.value = data

    def read(self):
        config = {}
        config_raw = to_unicode(self.data.value)
        if config_raw:
            config = json.loads(config_raw)
        return config
