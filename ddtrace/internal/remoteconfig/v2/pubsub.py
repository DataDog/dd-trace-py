import os

from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig.v2.connectors import ConnectorBase
from ddtrace.internal.remoteconfig.v2.publishers import RemoteConfigPublisherBase, RemoteConfigPublisherMergeFirst
from ddtrace.internal.remoteconfig.v2.subscribers import SubscriberBase

log = get_logger(__name__)


class PubSubBase:
    __publisher_class__ = RemoteConfigPublisherBase
    __subscriber_class__ = SubscriberBase
    __shared_data = ConnectorBase

    def __init__(self,  _preprocess_results, callback, name="Default"):
        log.debug("[%s][P: %s] PublisherListenerProxy created!!!", os.getpid(), os.getppid())
        self._publisher = self.__publisher_class__(self.__shared_data, _preprocess_results)
        self._subscriber = self.__subscriber_class__(self.__shared_data, callback, name)

    def start_listener(self):
        self._subscriber.force_restart()

    def stop(self):
        self._subscriber.stop()


class PubSubMergeMergeFirst(PubSubBase):
    __publisher_class__ = RemoteConfigPublisherMergeFirst
    __subscriber_class__ = SubscriberBase
    __shared_data = ConnectorBase

    def publish(self):
        self._publisher.dispatch()

    def append(self, target, config_content):
        self._publisher.append(target, config_content)

    def start_listener(self):
        self._subscriber.force_restart()

    def stop(self):
        self._subscriber.stop()
