import abc
import copy
import os
from typing import TYPE_CHECKING

import six

from ddtrace.internal.logger import get_logger


if TYPE_CHECKING:  # pragma: no cover
    from typing import Any
    from typing import Callable
    from typing import Optional

    from ddtrace.internal.remoteconfig.v2._pubsub import PubSubBase

log = get_logger(__name__)


class RemoteConfigPublisherBase(six.with_metaclass(abc.ABCMeta)):
    _preprocess_results_func = None  # type: Optional[Callable[[Any, Optional[PubSubBase]], Any]]

    def __init__(self, data_connector, preprocess_results):
        self._data_connector = data_connector
        self._preprocess_results_func = preprocess_results

    def dispatch(self, pubsub_instance):
        raise NotImplementedError

    def append(self, target, config_content):
        raise NotImplementedError

    def __call__(self, pubsub_instance, target, config_content):
        raise NotImplementedError


class RemoteConfigPublisher(RemoteConfigPublisherBase):
    def __init__(self, data_connector, preprocess_results):
        super(RemoteConfigPublisher, self).__init__(data_connector, preprocess_results)

    def __call__(self, pubsub_instance, metadata, config):
        # type: (Any, Optional[Any], Any) -> None
        from attrs import asdict

        if self._preprocess_results_func:
            config = self._preprocess_results_func(config, pubsub_instance)

        log.debug("[%s][P: %s] Publisher publish data: %s", os.getpid(), os.getppid(), str(config)[:100])
        self._data_connector.write(asdict(metadata), config)


class RemoteConfigPublisherMergeFirst(RemoteConfigPublisherBase):
    def __init__(self, data_connector, preprocess_results):
        super(RemoteConfigPublisherMergeFirst, self).__init__(data_connector, preprocess_results)
        self._configs = {}

    def append(self, target, config):
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

    def dispatch(self, pubsub_instance):
        config_result = {}
        try:
            for target, config in self._configs.items():
                for key, value in config.items():
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
            self._data_connector.write("", result)
        except Exception:
            log.debug("[%s]: PublisherAfterMerge error", os.getpid(), exc_info=True)
