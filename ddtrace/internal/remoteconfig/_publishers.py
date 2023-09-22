import abc
import copy
import os
from typing import TYPE_CHECKING

import six

from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Dict
    from typing import List
    from typing import Optional
    from typing import Tuple

    from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
    from ddtrace.internal.remoteconfig._pubsub import PubSub

    PreprocessFunc = Callable[[Dict[str, Any], Optional[PubSub]], Any]

log = get_logger(__name__)


class RemoteConfigPublisherBase(six.with_metaclass(abc.ABCMeta)):
    _preprocess_results_func = None  # type: Optional[PreprocessFunc]

    def __init__(self, data_connector, preprocess_func=None):
        # type: (PublisherSubscriberConnector, Optional[PreprocessFunc]) -> None
        self._data_connector = data_connector
        self._preprocess_results_func = preprocess_func

    def dispatch(self, pubsub_instance=None):
        # type: (Optional[Any]) -> None
        raise NotImplementedError

    def append(self, config_content, target, config_metadata):
        # type: (Optional[Any], str, Optional[Any]) -> None
        raise NotImplementedError


class RemoteConfigPublisher(RemoteConfigPublisherBase):
    """Standard Remote Config Publisher: each time Remote Config Client receives new payloads, RemoteConfigPublisher
    shared them to all process. Dynamic Instrumentation uses this class
    """

    def __init__(self, data_connector, preprocess_func=None):
        # type: (PublisherSubscriberConnector, Optional[PreprocessFunc]) -> None
        super(RemoteConfigPublisher, self).__init__(data_connector, preprocess_func)
        self._config_and_metadata = []  # type: List[Tuple[Optional[Any], Optional[Any]]]

    def append(self, config_content, target="", config_metadata=None):
        # type: (Optional[Any], str, Optional[Any]) -> None
        self._config_and_metadata.append((config_content, config_metadata))

    def dispatch(self, pubsub_instance=None):
        # type: (Optional[Any]) -> None
        from attr import asdict

        # TODO: RemoteConfigPublisher doesn't need _preprocess_results_func callback at this moment. Uncomment those
        #  lines if a new product need it
        #  if self._preprocess_results_func:
        #     config = self._preprocess_results_func(config, pubsub_instance)

        log.debug("[%s][P: %s] Publisher publish data: %s", os.getpid(), os.getppid(), self._config_and_metadata)

        self._data_connector.write(
            [asdict(metadata) if metadata else None for _, metadata in self._config_and_metadata],
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

    def append(self, config_content, target, config_metadata=None):
        # type: (Optional[Any], str, Optional[Any]) -> None
        if not self._configs.get(target):
            self._configs[target] = {}

        if config_content is False:
            # Remove old config from the configs dict. _remove_previously_applied_configurations function should
            # call to this method
            del self._configs[target]
        elif config_content is not None:
            # Append the new config to the configs dict. _load_new_configurations function should
            # call to this method
            if isinstance(config_content, dict):
                self._configs[target].update(config_content)
            else:
                raise ValueError("target %s config %s has type of %s" % (target, config_content, type(config_content)))

    def dispatch(self, pubsub_instance=None):
        # type: (Optional[Any]) -> None
        config_result = {}  # type: Dict[str, Any]
        try:
            for _target, config_item in self._configs.items():
                for key, value in config_item.items():
                    if isinstance(value, list):
                        config_result[key] = config_result.get(key, []) + value
                    elif isinstance(value, dict):
                        config_result[key] = value
                    else:
                        log.debug("[%s][P: %s] Invalid value  %s for key %s", os.getpid(), os.getppid(), value, key)
            result = copy.deepcopy(config_result)
            if self._preprocess_results_func:
                result = self._preprocess_results_func(result, pubsub_instance)
            log.debug("[%s][P: %s] PublisherAfterMerge publish %s", os.getpid(), os.getppid(), str(result)[:100])
            self._data_connector.write({}, result)
        except Exception:
            log.error("[%s]: PublisherAfterMerge error", os.getpid(), exc_info=True)
