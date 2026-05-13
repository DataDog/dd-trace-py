from dataclasses import asdict
import json
import os
import time
from typing import Sequence
from uuid import UUID

from ddtrace.internal.compat import get_mp_context
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import Payload


log = get_logger(__name__)

# Size of the shared variable.
# It must be large enough to receive at least 2500 IPs or 2500 users to block.
SHARED_MEMORY_SIZE = 0x100000

SharedDataType = list[Payload]


class UUIDEncoder(json.JSONEncoder):
    def default(self, o):
        if isinstance(o, UUID):
            # if the obj is uuid, we simply return the value of uuid
            return o.hex
        return json.JSONEncoder.default(self, o)


class _DummySharedArray:
    """Dummy shared array to be used when shared memory is not available.
    This class is used to avoid breaking the code when shared memory is not available.
    """

    def __init__(self):
        self.value = b""


class PublisherSubscriberConnector:
    """ "PublisherSubscriberConnector is the bridge between Publisher and Subscriber class that uses an array of chars
    to share information between processes. `multiprocessing.Array``, as far as we know, was the most efficient way to
    share information. We compare this approach with: Multiprocess Manager, Multiprocess Value, Multiprocess Queues
    """

    def __init__(self):
        from ddtrace.internal.serverless import in_aws_lambda

        if in_aws_lambda():
            self.data = _DummySharedArray()
        else:
            try:
                self.data = get_mp_context().Array("c", SHARED_MEMORY_SIZE, lock=False)
            # FileNotFoundError: /dev/shm may not exist or be inaccessible.
            # ImportError: multiprocessing.Array imports multiprocessing.sharedctypes,
            # which imports ctypes and requires the _ctypes C extension module. Some
            # environments (e.g. Alpine Linux, minimal Docker images, or custom
            # Python builds without libffi) do not provide _ctypes and raise
            # ModuleNotFoundError.
            # See: https://app.datadoghq.com/error-tracking/issue/25b34008-bb9f-11f0-abbd-da7ad0900002
            except (FileNotFoundError, ImportError):
                log.warning(
                    "Unable to create shared memory. "
                    "Features relying on remote configuration will not work as expected."
                )
                self.data = _DummySharedArray()
        # Checksum attr validates if the Publisher send new data
        self.checksum = -1
        # shared_data_counter attr validates if the Subscriber send new data
        self.shared_data_counter = 0
        self.read_pid = os.getpid()

    @staticmethod
    def _hash_config(payload_sequence: Sequence[Payload]):
        result = 0
        for payload in payload_sequence:
            result ^= hash(payload.metadata)
            if payload.content is None:
                result <<= 1
        return result

    def read(self) -> SharedDataType:
        config_raw = self.data.value.decode("utf-8", errors="ignore")
        config = json.loads(config_raw) if config_raw else None
        if config is not None:
            shared_data_counter = config["shared_data_counter"]
            if (current_pid := os.getpid()) != self.read_pid:
                self.read_pid = current_pid
                self.shared_data_counter = 0
            if shared_data_counter != self.shared_data_counter:
                self.shared_data_counter = shared_data_counter
                return [Payload(**value) for value in config["payload_list"]]
        return []

    def write(self, payload_list: Sequence[Payload]) -> None:
        last_checksum = self._hash_config(payload_list)
        if last_checksum != self.checksum:
            data = self.serialize(payload_list)
            data_len = len(data)
            if data_len >= (SHARED_MEMORY_SIZE - 1000):
                log.warning("Datadog Remote Config shared data is %s/%s", data_len, SHARED_MEMORY_SIZE)
            self.data.value = data
            log.debug("[%s][P: %s] Write message of length %s", os.getpid(), os.getppid(), data_len)
            self.checksum = last_checksum

    @staticmethod
    def serialize(payload_list: Sequence[Payload]) -> bytes:
        return json.dumps(
            {"payload_list": [asdict(p) for p in payload_list], "shared_data_counter": time.monotonic_ns()},
            cls=UUIDEncoder,
            ensure_ascii=False,
        ).encode()
