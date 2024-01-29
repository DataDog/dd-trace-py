import os

from ddtrace.internal.forksafe import has_forked
from ddtrace.internal.logger import get_logger
from ddtrace.internal.remoteconfig._connectors import PublisherSubscriberConnector
from ddtrace.internal.remoteconfig._publishers import RemoteConfigPublisher
from ddtrace.internal.remoteconfig._pubsub import PubSub
from ddtrace.internal.remoteconfig._subscribers import RemoteConfigSubscriber
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.runtime import get_ancestor_runtime_id
from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader


log = get_logger(__name__)


def _rc_callback(data, test_tracer=None):
    if get_ancestor_runtime_id() is not None and has_forked():
        log.debug("[PID %d] SymDB: Disabling Symbol DB in forked process", os.getpid())
        # We assume that forking is being used for spawning child worker
        # processes. Therefore, we avoid uploading the same symbols from each
        # child process. We restrict the enablement of Symbol DB to just the
        # parent process and the first fork child.
        remoteconfig_poller.unregister("LIVE_DEBUGGING_SYMBOL_DB")

        if SymbolDatabaseUploader.is_installed():
            SymbolDatabaseUploader.uninstall()

        return

    for metadata, config in zip(data["metadata"], data["config"]):
        if metadata is None:
            continue

        if isinstance(config, dict) and config.get("upload_symbols", False):
            if not SymbolDatabaseUploader.is_installed():
                log.debug("[PID %d] SymDB: Symbol DB RCM enablement signal received", os.getpid())
                try:
                    SymbolDatabaseUploader.install()
                except Exception:
                    log.error("[PID %d] SymDB: Failed to install Symbol DB uploader", os.getpid(), exc_info=True)
                    remoteconfig_poller.unregister("LIVE_DEBUGGING_SYMBOL_DB")
            return


class SymbolDatabaseAdapter(PubSub):
    __publisher_class__ = RemoteConfigPublisher
    __subscriber_class__ = RemoteConfigSubscriber
    __shared_data__ = PublisherSubscriberConnector()

    def __init__(self):
        self._publisher = self.__publisher_class__(self.__shared_data__)
        self._subscriber = self.__subscriber_class__(self.__shared_data__, _rc_callback, "LIVE_DEBUGGING_SYMBOL_DB")
