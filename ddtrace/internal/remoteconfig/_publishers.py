import abc
import dataclasses
import os
from typing import TYPE_CHECKING  # noqa:F401
from typing import Any
from typing import Dict
from typing import List
from typing import Optional
from typing import Tuple

from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:  # pragma: no cover
    from typing import Callable

    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector  # noqa:F401
    from ddtrace.internal.remoteconfig._pubsub import PubSub

    PreprocessFunc = Callable[[Dict[str, Any], Optional[PubSub]], Any]

log = get_logger(__name__)


class RemoteConfigPublisherBase(metaclass=abc.ABCMeta):
    _preprocess_results_func = None  # type: Optional[PreprocessFunc]

    def __init__(self, data_connector, preprocess_func=None):
        # type: (PublisherSubscriberConnector, Optional[PreprocessFunc]) -> None
        self._data_connector = data_connector
        self._preprocess_results_func = preprocess_func

    def dispatch(self, pubsub_instance: Optional[Any] = None) -> None:
        raise NotImplementedError

    def append(self, config_content: Optional[Any], target: str, config_metadata: Optional[Any]) -> None:
        raise NotImplementedError


class RemoteConfigPublisher(RemoteConfigPublisherBase):
    """Standard Remote Config Publisher: each time Remote Config Client receives new payloads, RemoteConfigPublisher
    shared them to all process. Dynamic Instrumentation uses this class
    """

    def __init__(self, data_connector, preprocess_func=None):
        # type: (PublisherSubscriberConnector, Optional[PreprocessFunc]) -> None
        super(RemoteConfigPublisher, self).__init__(data_connector, preprocess_func)
        self._config_and_metadata: List[Tuple[Optional[Any], Optional[Any]]] = []

    def append(self, config_content: Optional[Any], target: str = "", config_metadata: Optional[Any] = None) -> None:
        self._config_and_metadata.append((config_content, config_metadata))

    def dispatch(self, pubsub_instance: Optional[Any] = None) -> None:
        # TODO: RemoteConfigPublisher doesn't need _preprocess_results_func callback at this moment. Uncomment those
        #  lines if a new product need it
        #  if self._preprocess_results_func:
        #     config = self._preprocess_results_func(config, pubsub_instance)

        log.debug("[%s][P: %s] Publisher publish data: %s", os.getpid(), os.getppid(), self._config_and_metadata)

        self._data_connector.write(
            [dataclasses.asdict(metadata) if metadata else None for _, metadata in self._config_and_metadata],
            [config for config, _ in self._config_and_metadata],
        )
        self._config_and_metadata = []


class RemoteConfigPublisherMergeDicts(RemoteConfigPublisherBase):
    """Each time Remote Config Client receives a new payload, Publisher stores the target file path and its payload.
    When the Client finishes to update/add the configuration, Client calls to `publisher.dispatch` which merges all
    payloads and send it to the subscriber. ASM uses this class
    """

    def __init__(self, data_connector, preprocess_func):
        # type: (PublisherSubscriberConnector, PreprocessFunc) -> None
        super(RemoteConfigPublisherMergeDicts, self).__init__(data_connector, preprocess_func)
        self._configs = {}  # type: Dict[str, Any]

    def append(self, config_content: Optional[Any], target: str, config_metadata: Optional[Any] = None) -> None:
        if target not in self._configs:
            self._configs[target] = {}

        if config_content is False:
            # clear non empty values but keep the keys active so it can be updated accordingly
            for k, v in self._configs[target].items():
                if v:
                    if isinstance(v, list):
                        self._configs[target][k] = []
                    elif isinstance(v, dict):
                        self._configs[target][k] = {}
                    else:
                        self._configs[target][k] = None
        elif config_content is not None:
            # Append the new config to the configs dict. _load_new_configurations function should
            # call to this method
            if isinstance(config_content, dict):
                self._configs[target].update(config_content)
            else:
                log.warning("target %s config %s has type of %s", target, config_content, type(config_content))

    def dispatch(self, pubsub_instance: Optional[Any] = None) -> None:
        result: Dict[str, Any] = {}
        try:
            for _target, config_item in self._configs.items():
                for key, value in config_item.items():
                    # We are merging lists and dictionaries from several payloads
                    if isinstance(value, list):
                        result[key] = result.get(key, []) + value
                    elif isinstance(value, dict):
                        if key not in result:
                            result[key] = {}
                        for k, v in value.items():
                            if k in result[key]:
                                if result[key][k] != v:
                                    info = (
                                        f"[{os.getpid()}][P: {os.getppid()}]"
                                        f"Conflicting values {result[key][k]} and {v} for key {key}"
                                    )
                                    log.debug(info)
                            else:
                                result[key][k] = v
                    else:
                        result[key] = value
                        log.debug("[%s][P: %s] Invalid value  %s for key %s", os.getpid(), os.getppid(), value, key)
            if self._preprocess_results_func:
                result = self._preprocess_results_func(result, pubsub_instance)
            log.debug("[%s][P: %s] PublisherAfterMerge publish %s", os.getpid(), os.getppid(), str(result)[:100])
            self._data_connector.write({}, result)
        except Exception:
            log.error("[%s]: PublisherAfterMerge error", os.getpid(), exc_info=True)
