import abc
import os
from typing import TYPE_CHECKING  # noqa:F401
from typing import List

from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import PayloadType


if TYPE_CHECKING:  # pragma: no cover
    from typing import Callable  # noqa:F401
    from typing import Optional  # noqa:F401

    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401
    from ddtrace.internal.remoteconfig._pubsub import PubSub

    PreprocessFunc = Callable[[List[Payload], PubSub], List[Payload]]

log = get_logger(__name__)


class RemoteConfigPublisherBase(metaclass=abc.ABCMeta):
    _preprocess_results_func = None  # type: Optional[PreprocessFunc]

    def __init__(self, data_connector, preprocess_func=None):
        # type: (PublisherSubscriberConnector, Optional[PreprocessFunc]) -> None
        self._data_connector = data_connector
        self._preprocess_results_func = preprocess_func

    def dispatch(self, pubsub_instance: "PubSub") -> None:
        raise NotImplementedError

    def append(self, config_content: PayloadType, target: str, config_metadata: ConfigMetadata) -> None:
        raise NotImplementedError


class RemoteConfigPublisher(RemoteConfigPublisherBase):
    """Standard Remote Config Publisher: each time Remote Config Client receives new payloads, RemoteConfigPublisher
    shared them to all process. Dynamic Instrumentation uses this class
    """

    def __init__(self, data_connector, preprocess_func=None):
        # type: (PublisherSubscriberConnector, Optional[PreprocessFunc]) -> None
        super(RemoteConfigPublisher, self).__init__(data_connector, preprocess_func)
        self._config_and_metadata: List[Payload] = []

    def append(self, config_content: PayloadType, target: str, config_metadata: ConfigMetadata) -> None:
        self._config_and_metadata.append(Payload(config_metadata, target, config_content))

    def dispatch(self, pubsub_instance: "PubSub") -> None:
        if self._preprocess_results_func:
            self._config_and_metadata = list(self._preprocess_results_func(self._config_and_metadata, pubsub_instance))

        log.debug("[%s][P: %s] Publisher publish data: %s", os.getpid(), os.getppid(), self._config_and_metadata)

        self._data_connector.write(self._config_and_metadata)
        self._config_and_metadata = []
