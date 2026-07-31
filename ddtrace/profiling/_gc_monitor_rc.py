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
        """
        Example payload:
        {
            "poc": "reference_chains",
            "contents": {
                "enabled": true,
                "referrers_enabled": true
            }
        }
        """
        # Piggy-backing on the `Debug` product until a dedicated
        # product/schema is defined. `contents.enabled` toggles the GC monitor
        # on/off; `contents.referrers_enabled` controls whether type walking /
        # full reference chains are tracked. Payloads missing these fields (or
        # deleted payloads) leave the state unchanged.
        for payload in payloads:
            log.error(
                "GC monitor RC payload received: path=%s metadata=%r content=%r",
                payload.path,
                payload.metadata,
                payload.content,
            )

        desired_enabled: Optional[bool] = None
        desired_referrers: Optional[bool] = None
        for payload in payloads:
            content = payload.content
            if not isinstance(content, dict):
                continue
            contents = content.get("contents")
            if not isinstance(contents, dict):
                continue
            enabled = contents.get("enabled")
            if isinstance(enabled, bool):
                desired_enabled = enabled
            referrers = contents.get("referrers_enabled")
            if isinstance(referrers, bool):
                desired_referrers = referrers

        if desired_enabled is None:
            return

        if desired_enabled and not self._started:
            try:
                log.error(
                    "Starting GC monitor via remote config (interval=%ds, referrers_enabled=%s)",
                    profiling_config.gc.interval_s,
                    bool(desired_referrers),
                )
                ddup.start_gc_monitor(
                    interval_ms=profiling_config.gc.interval_s * 1000,
                    survivor_threshold=profiling_config.gc.survivor_threshold,
                    top_n=profiling_config.gc.top_n,
                    referrers_enabled=bool(desired_referrers),
                    max_depth=profiling_config.gc.max_depth,
                )
                self._started = True
                log.debug(
                    "GC monitor started via remote config (interval=%ds, referrers_enabled=%s)",
                    profiling_config.gc.interval_s,
                    bool(desired_referrers),
                )
            except Exception:
                log.error("Failed to start GC monitor from remote config", exc_info=True)
        elif not desired_enabled and self._started:
            try:
                log.error("Stopping GC monitor via remote config")
                ddup.stop_gc_monitor()
                self._started = False
                log.debug("GC monitor stopped via remote config")
            except Exception:
                log.error("Failed to stop GC monitor from remote config", exc_info=True)


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
