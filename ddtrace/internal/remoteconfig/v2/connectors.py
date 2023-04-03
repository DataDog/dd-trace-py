import abc
import json
import multiprocessing
import os
from ctypes import c_char

import six

ROOT_DIR = os.path.dirname(os.path.abspath(__file__))
SHARED_DATA_FILE = os.path.join(ROOT_DIR, "shared_data.json")


class ConnectorBase(six.with_metaclass(abc.ABCMeta)):
    @abc.abstractmethod
    def read(self):
        pass

    @abc.abstractmethod
    def write(self, data):
        pass


class ConnectorFile(ConnectorBase):
    def write(self, data):
        fl = open(SHARED_DATA_FILE, "w")
        fl.write(json.dumps(data))
        fl.close()

    def read(self):
        fl = open(SHARED_DATA_FILE)
        data = fl.read()
        fl.close()
        return json.loads(data)


class ConnectorSharedMemory(ConnectorBase):
    def __init__(self):
       self.data = multiprocessing.Array(c_char, 603432, lock=False)

    def write(self, data_raw):
        data = bytes(json.dumps(data_raw), encoding="utf-8")
        self.data.value = data

    def read(self):
        data = {}
        data_raw = str(self.data.value, encoding="utf-8")
        if data_raw:
            data = json.loads(data_raw)
        return data