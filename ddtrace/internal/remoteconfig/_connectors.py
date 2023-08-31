from ctypes import c_char
import json
import multiprocessing
import os
import sys
from typing import Any
from typing import Dict
from typing import Mapping
from uuid import UUID

from ddtrace.internal.compat import PY2
from ddtrace.internal.compat import to_unicode
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# Size of the shared variable. It's calculated based on Remote Config Payloads. At 2023-04-26 we measure on stagging
# RC payloads and the max size of a multiprocess.array was 139.002 (sys.getsizeof(data.value)) and
# max len 138.969 (len(data.value))
SHARED_MEMORY_SIZE = 603432

SharedDataType = Mapping[str, Any]


class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        # type: (Any) -> Any
        if isinstance(obj, UUID):
            # if the obj is uuid, we simply return the value of uuid
            return obj.hex
        return json.JSONEncoder.default(self, obj)


class PublisherSubscriberConnector(object):
    """ "PublisherSubscriberConnector is the bridge between Publisher and Subscriber class that uses an array of chars
    to share information between processes. `multiprocessing.Array``, as far as we know, was the most efficient way to
    share information. We compare this approach with: Multiprocess Manager, Multiprocess Value, Multiprocess Queues
    """

    def __init__(self):
        self.data = multiprocessing.Array(c_char, SHARED_MEMORY_SIZE, lock=False)
        # Checksum attr validates if the Publisher send new data
        self.checksum = -1
        # shared_data_counter attr validates if the Subscriber send new data
        self.shared_data_counter = 0

    @staticmethod
    def _hash_config(config_raw, metadata_raw):
        # type: (Any, Any) -> int
        return hash(str(config_raw) + str(metadata_raw))

    def read(self):
        # type: () -> SharedDataType
        config_raw = to_unicode(self.data.value)
        config = json.loads(config_raw) if config_raw else {}
        if config:
            shared_data_counter = config["shared_data_counter"]
            if shared_data_counter > self.shared_data_counter:
                self.shared_data_counter += 1
                return config
        return {}

    def write(self, metadata, config_raw):
        # type: (Any, Any) -> None
        last_checksum = self._hash_config(config_raw, metadata)
        if last_checksum != self.checksum:
            data_len = len(self.data.value)
            if data_len >= (SHARED_MEMORY_SIZE - 1000):
                log.warning("Datadog Remote Config shared data is %s/%s", data_len, SHARED_MEMORY_SIZE)
            data = self.serialize(metadata, config_raw, self.shared_data_counter + 1)
            self.data.value = data
            log.debug(
                "[%s][P: %s] write message of size %s and len %s",
                os.getpid(),
                os.getppid(),
                sys.getsizeof(self.data.value),
                data_len,
            )
            self.checksum = last_checksum

    @staticmethod
    def serialize(metadata, config_raw, shared_data_counter):
        # type: (Any, Dict[str, Any], int) -> bytes
        if PY2:
            data = bytes(
                json.dumps(
                    {"metadata": metadata, "config": config_raw, "shared_data_counter": shared_data_counter},
                    cls=UUIDEncoder,
                )
            )
        else:
            data = bytes(
                json.dumps(
                    {"metadata": metadata, "config": config_raw, "shared_data_counter": shared_data_counter},
                    cls=UUIDEncoder,
                ),
                encoding="utf-8",
            )
        return data
