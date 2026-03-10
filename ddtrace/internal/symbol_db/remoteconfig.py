import os
import typing as t

from ddtrace.internal.forksafe import has_forked
from ddtrace.internal.ipc import SharedStringFile
from ddtrace.internal.logger import get_logger
from ddtrace.internal.products import manager as product_manager
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.runtime import get_ancestor_runtime_id
from ddtrace.internal.symbol_db.symbols import SymbolDatabaseUploader


DI_PRODUCT_KEY = "dynamic-instrumentation"

log = get_logger(__name__)

# Use a shared file to keep track of which PIDs have Symbol DB enabled. This way
# we can ensure that at most two processes are emitting symbols under a large
# range of scenarios.
shared_pid_file = SharedStringFile(f"{os.getppid()}-symdb-pids")

MAX_CHILD_UPLOADERS = 1  # max one child


class SymbolDatabaseCallback(RCCallback):
    """Remote config callback for symbol database management."""

    def __call__(self, payloads: t.Sequence[Payload]) -> None:
        """Process symbol database configuration payloads.

        Args:
            payloads: Sequence of configuration payloads to process
        """
        with shared_pid_file.lock_exclusive() as f:
            if (pid := str(os.getpid())) not in (pids := set(shared_pid_file.peekall_unlocked(f))):
                # Store the PID of the current process so that we know which processes
                # have Symbol DB enabled.
                shared_pid_file.put_unlocked(f, pid)

            if (get_ancestor_runtime_id() is not None and has_forked()) or len(
                pids - {pid, str(os.getppid())}
            ) >= MAX_CHILD_UPLOADERS:
                log.debug("[PID %d] SymDB: Disabling Symbol DB in child process", os.getpid())
                # We assume that forking is being used for spawning child worker
                # processes. Therefore, we avoid uploading the same symbols from each
                # child process. We restrict the enablement of Symbol DB to just the
                # parent process and the first fork child.
                remoteconfig_poller.unregister_callback("LIVE_DEBUGGING_SYMBOL_DB")
                remoteconfig_poller.disable_product("LIVE_DEBUGGING_SYMBOL_DB")

                if SymbolDatabaseUploader.is_installed():
                    SymbolDatabaseUploader.uninstall()

                return

        for payload in payloads:
            if payload.metadata is None:
                continue

            config = payload.content
            if not isinstance(config, dict):
                continue

            upload_symbols = config.get("upload_symbols")
            if upload_symbols is None:
                continue

            if upload_symbols:
                log.debug("[PID %d] SymDB: Symbol DB RCM enablement signal received", os.getpid())
                if not SymbolDatabaseUploader.is_installed():
                    try:
                        SymbolDatabaseUploader.install(shallow=not product_manager.is_enabled(DI_PRODUCT_KEY))
                        log.debug("[PID %d] SymDB: Symbol DB uploader installed", os.getpid())
                    except Exception:
                        log.error("[PID %d] SymDB: Failed to install Symbol DB uploader", os.getpid(), exc_info=True)
                        remoteconfig_poller.unregister_callback("LIVE_DEBUGGING_SYMBOL_DB")
                        remoteconfig_poller.disable_product("LIVE_DEBUGGING_SYMBOL_DB")
                else:
                    SymbolDatabaseUploader.update()
            else:
                log.debug("[PID %d] SymDB: Symbol DB RCM shutdown signal received", os.getpid())
                if SymbolDatabaseUploader.is_installed():
                    try:
                        SymbolDatabaseUploader.uninstall()
                        log.debug("[PID %d] SymDB: Symbol DB uploader uninstalled", os.getpid())
                    except Exception:
                        log.error("[PID %d] SymDB: Failed to uninstall Symbol DB uploader", os.getpid(), exc_info=True)
                        remoteconfig_poller.unregister_callback("LIVE_DEBUGGING_SYMBOL_DB")
                        remoteconfig_poller.disable_product("LIVE_DEBUGGING_SYMBOL_DB")
            break


# Keep the old name for compatibility but mark as deprecated
_rc_callback = SymbolDatabaseCallback()
