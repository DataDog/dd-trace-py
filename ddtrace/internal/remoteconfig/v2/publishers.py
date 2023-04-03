import abc
import copy
import os
from typing import Any
from typing import Optional

import six

from ddtrace.internal.logger import get_logger

log = get_logger(__name__)


class RemoteConfigPublisherBase(six.with_metaclass(abc.ABCMeta)):
    _preprocess_results_func = None

    def __init__(self, data_connector, preprocess_results):
        self._data_connector = data_connector
        self._preprocess_results_func = preprocess_results

    @abc.abstractmethod
    def dispatch(self):
        pass

    @abc.abstractmethod
    def append(self, target, config_content):
        pass


class RemoteConfigPublisher(RemoteConfigPublisherBase):
    def __init__(self, data_connector, preprocess_results):
        super().__init__(data_connector, preprocess_results)

    def __call__(self, metadata, config):
        # type: (Optional[Any], Any) -> None
        try:
            if self._preprocess_results_func:
                config = self._preprocess_results_func(config)
            if type(config) == dict:
                log.debug("[%s][P: %s] Publisher share data %s", os.getpid(), os.getppid(), str(config)[:100])
                self._data_connector.write(config)
        except Exception as e:
            log.debug("[%s]: Publisher error", os.getpid(), exc_info=True)


class RemoteConfigPublisherMergeFirst(RemoteConfigPublisherBase):

    def __init__(self, data_connector, preprocess_results):
        super().__init__(data_connector, preprocess_results)
        self.configs = {}

    def append(self, target, config):
        if not self.configs.get(target):
            self.configs[target] = {}
        if config is False:
            # Remove old config from the configs dict. _remove_previously_applied_configurations function should
            # call to this method
            del self.configs[target]
        elif config is not None:
            # Append the new config to the configs dict. _load_new_configurations function should
            # call to this method
            if isinstance(config, dict):
                self.configs[target].update(config)
            else:
                raise ValueError("target %s config %s has type of %s" % (target, config, type(config)))

    def dispatch(self):
        config_result = {}
        try:
            for target, config in self.configs.items():
                for key, value in config.items():
                    if isinstance(value, list):
                        config_result[key] = config_result.get(key, []) + value
                    elif isinstance(value, dict):
                        print("DISPATCH {} {}".format(key, value))
                        config_result[key] = value
                    else:
                        pass
                        # raise ValueError(
                        #     "target %s key %s has type of %s.\nvalue %s" % (target, key, type(value), value)
                        # )
            if config_result:
                result = copy.deepcopy(config_result)
                if self._preprocess_results_func:
                    result = self._preprocess_results_func(result)
                log.debug("[%s][P: %s] PublisherAfterMerge share data %s", os.getpid(), os.getppid(), str(result)[:100])
                self._data_connector.write(result)
        except Exception:
            log.debug("[%s]: PublisherAfterMerge error", os.getpid(), exc_info=True)
