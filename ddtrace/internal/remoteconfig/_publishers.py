import abc
from collections.abc import Callable
import os
from typing import TYPE_CHECKING  # noqa:F401
from typing import Optional

from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import PayloadType


if TYPE_CHECKING:  # pragma: no cover
    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401

    PreprocessFunc = Callable[[list[Payload]], list[Payload]]

log = get_logger(__name__)


class RemoteConfigPublisherBase(metaclass=abc.ABCMeta):
    _preprocess_results_func: Optional["PreprocessFunc"] = None

    def __init__(
        self, data_connector: "PublisherSubscriberConnector", preprocess_func: Optional["PreprocessFunc"] = None
    ) -> None:
        self._data_connector = data_connector
        self._preprocess_results_func = preprocess_func

    def dispatch(self) -> None:
        raise NotImplementedError

    def append(self, config_content: PayloadType, target: str, config_metadata: ConfigMetadata) -> None:
        raise NotImplementedError


class RemoteConfigPublisher(RemoteConfigPublisherBase):
    """Standard Remote Config Publisher: each time Remote Config Client receives new payloads, RemoteConfigPublisher
    shared them to all process. Dynamic Instrumentation uses this class
    """

    def __init__(
        self, data_connector: "PublisherSubscriberConnector", preprocess_func: Optional["PreprocessFunc"] = None
    ) -> None:
        super(RemoteConfigPublisher, self).__init__(data_connector, preprocess_func)
        self._config_and_metadata: list[Payload] = []

    def append(self, config_content: PayloadType, target: str, config_metadata: ConfigMetadata) -> None:
        self._config_and_metadata.append(Payload(config_metadata, target, config_content))

    def dispatch(self) -> None:
        if self._preprocess_results_func:
            self._config_and_metadata = list(self._preprocess_results_func(self._config_and_metadata))

        log.debug("[%s][P: %s] Publisher publish data: %s", os.getpid(), os.getppid(), self._config_and_metadata)

        self._data_connector.write(self._config_and_metadata)
        self._config_and_metadata = []
