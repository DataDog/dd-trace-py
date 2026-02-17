import abc
import os
from typing import TYPE_CHECKING  # noqa:F401

from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import PayloadType


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401

log = get_logger(__name__)


class RemoteConfigPublisherBase(metaclass=abc.ABCMeta):
    def __init__(self, data_connector: "PublisherSubscriberConnector") -> None:
        self._data_connector = data_connector

    def dispatch(self) -> None:
        raise NotImplementedError

    def append(self, config_content: PayloadType, target: str, config_metadata: ConfigMetadata) -> None:
        raise NotImplementedError


class RemoteConfigPublisher(RemoteConfigPublisherBase):
    """Standard Remote Config Publisher: each time Remote Config Client receives new payloads, RemoteConfigPublisher
    shared them to all process. Dynamic Instrumentation uses this class
    """

    def __init__(self, data_connector: "PublisherSubscriberConnector") -> None:
        super(RemoteConfigPublisher, self).__init__(data_connector)
        self._config_and_metadata: list[Payload] = []

    def append(self, config_content: PayloadType, target: str, config_metadata: ConfigMetadata) -> None:
        self._config_and_metadata.append(Payload(config_metadata, target, config_content))

    def dispatch(self) -> None:
        log.debug("[%s][P: %s] Publisher publish data: %s", os.getpid(), os.getppid(), self._config_and_metadata)

        self._data_connector.write(self._config_and_metadata)
        self._config_and_metadata = []
