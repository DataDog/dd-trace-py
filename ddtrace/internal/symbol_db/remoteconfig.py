from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader


def _rc_callback(data, test_tracer=None):
    for metadata, config in zip(data["metadata"], data["config"]):
        if metadata is None:
            continue

        if config.get("upload_symbols", False):
            if not SymbolDatabaseUploader.is_installed():
                SymbolDatabaseUploader.install()
            return


class SymbolDatabaseAdapter(PubSub):
    __publisher_class__ = RemoteConfigPublisher
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self):
        self._publisher = self.__publisher_class__(self.__shared_data__)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, _rc_callback, "LIVE_DEBUGGING_SYMBOL_DB")
