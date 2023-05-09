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
    from typing import Optional

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

    def dispatch(self, config, metadata=None, pubsub_instance=None):
        # type: (Any, Optional[Any], Optional[Any]) -> None
        raise NotImplementedError

    def append(self, target, config_content):
        # type: (str, Optional[Any]) -> None
        raise NotImplementedError


class RemoteConfigPublisher(RemoteConfigPublisherBase):
    """Standard Remote Config Publisher: each time Remote Config Client receives a new payload, RemoteConfigPublisher
    shared it to all process.
    """

    def __init__(self, data_connector, preprocess_func=None):
        # type: (PublisherSubscriberConnector, Optional[PreprocessFunc]) -> None
        super(RemoteConfigPublisher, self).__init__(data_connector, preprocess_func)

    def dispatch(self, config, metadata=None, pubsub_instance=None):
        # type: (Any, Optional[Any], Optional[Any]) -> None
        from attr import asdict

        if self._preprocess_results_func:
            config = self._preprocess_results_func(config, pubsub_instance)

        log.debug("[%s][P: %s] Publisher publish data: %s", os.getpid(), os.getppid(), str(config)[:100])
        if metadata:
            metadata = asdict(metadata)
        self._data_connector.write(metadata, config)


class RemoteConfigPublisherMergeFirst(RemoteConfigPublisherBase):
    """Each time Remote Config Client receives a new payload, Publisher stores the target file path and its payload.
    When the Client finishes to update/add the configuration, Client calls to `publisher.dispatch` which merges all
    payloads and send it to the subscriber
    """

    def __init__(self, data_connector, preprocess_func):
        # type: (PublisherSubscriberConnector, PreprocessFunc) -> None
        super(RemoteConfigPublisherMergeFirst, self).__init__(data_connector, preprocess_func)
        self._configs = {}  # type: Dict[str, Any]

    def append(self, target, config):
        # type: (str, Optional[Any]) -> None
        if not self._configs.get(target):
            self._configs[target] = {}

        if config is False:
            # Remove old config from the configs dict. _remove_previously_applied_configurations function should
            # call to this method
            del self._configs[target]
        elif config is not None:
            # Append the new config to the configs dict. _load_new_configurations function should
            # call to this method
            if isinstance(config, dict):
                self._configs[target].update(config)
            else:
                raise ValueError("target %s config %s has type of %s" % (target, config, type(config)))

    def dispatch(self, config=None, metadata=None, pubsub_instance=None):
        # type: (Any, Optional[Any], Optional[Any]) -> None
        config_result = {}  # type: Dict[str, Any]
        try:
            for target, config_item in self._configs.items():
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
