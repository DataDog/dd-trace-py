from typing import Optional
from typing import Sequence

from ddtrace.internal.datadog.profiling import ddup
from ddtrace.internal.logger import get_logger
from ddtrace.internal.native import RemoteConfigProduct
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.remoteconfig.worker import remoteconfig_poller
from ddtrace.internal.settings.profiling import config as profiling_config


log = get_logger(__name__)


class _GCMonitorRCCallback(RCCallback):
    def __init__(self, already_started: bool = False) -> None:
        self._started: bool = already_started

    def __call__(self, payloads: Sequence[Payload]) -> None:
        # Piggy-backing on the `Debug` product until a dedicated
        # product/schema is defined. Any non-empty payload flips the switch on;
        # once started we stay started for the lifetime of the process.
        if self._started or not payloads:
            return
        try:
            ddup.start_gc_monitor(
                interval_ms=profiling_config.gc.interval_s * 1000,
                survivor_threshold=profiling_config.gc.survivor_threshold,
                top_n=profiling_config.gc.top_n,
                referrers_enabled=profiling_config.gc.referrers_enabled,
                max_depth=profiling_config.gc.max_depth,
            )
            self._started = True
            log.debug(
                "GC monitor started via remote config (interval=%ds)",
                profiling_config.gc.interval_s,
            )
        except Exception:
            log.error("Failed to start GC monitor from remote config", exc_info=True)


_callback: Optional[_GCMonitorRCCallback] = None


def start() -> None:
    global _callback
    if _callback is not None:
        return
    _callback = _GCMonitorRCCallback(already_started=profiling_config.gc.enabled)
    remoteconfig_poller.register_callback(RemoteConfigProduct.Debug, _callback)
    remoteconfig_poller.enable_product(RemoteConfigProduct.Debug)


def stop() -> None:
    global _callback
    if _callback is None:
        return
    remoteconfig_poller.unregister_callback(RemoteConfigProduct.Debug)
    remoteconfig_poller.disable_product(RemoteConfigProduct.Debug)
    # If the callback started the GC monitor and the env-configured path did
    # not, the profiler's own stop path will skip stop_gc_monitor -- do it here.
    if _callback._started and not profiling_config.gc.enabled:
        try:
            ddup.stop_gc_monitor()
        except Exception:
            log.debug("Exception while stopping GC monitor from remote config", exc_info=True)
    _callback = None
