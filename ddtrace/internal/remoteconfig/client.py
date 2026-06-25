import json
import os
from typing import TYPE_CHECKING
from typing import Any
from typing import Optional
from typing import Sequence
import uuid

import ddtrace
from ddtrace.internal import gitmetadata
from ddtrace.internal import process_tags
from ddtrace.internal import runtime
from ddtrace.internal.hostname import get_hostname
from ddtrace.internal.logger import get_logger
from ddtrace.internal.packages import is_distribution_available
from ddtrace.internal.remoteconfig import ConfigMetadata
from ddtrace.internal.remoteconfig import Payload
from ddtrace.internal.remoteconfig import PayloadType
from ddtrace.internal.remoteconfig import RCCallback
from ddtrace.internal.settings._agent import config as agent_config
from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.telemetry import telemetry_writer
from ddtrace.internal.telemetry.constants import TELEMETRY_LOG_LEVEL
from ddtrace.internal.utils.time import StopWatch
from ddtrace.internal.utils.version import _pep440_to_semver


if TYPE_CHECKING:
    from ddtrace.internal.native import RemoteConfigProduct


log = get_logger(__name__)

# Threshold for callback execution time warning (in seconds)
CALLBACK_EXECUTION_WARNING_THRESHOLD = 0.5


REQUIRE_SKIP_SHUTDOWN = frozenset({"django-q"})


def derive_skip_shutdown(c: "RemoteConfigClientConfig") -> bool:
    return (
        c._skip_shutdown
        if c._skip_shutdown is not None
        else any(is_distribution_available(_) for _ in REQUIRE_SKIP_SHUTDOWN)
    )


class RemoteConfigClientConfig(DDConfig):
    __prefix__ = "_dd.remote_configuration"

    log_payloads = DDConfig.v(bool, "log_payloads", default=False)

    _skip_shutdown = DDConfig.v(Optional[bool], "skip_shutdown", default=None)
    skip_shutdown = DDConfig.d(bool, derive_skip_shutdown)


config = RemoteConfigClientConfig()


def _build_tags(tracer_version: str) -> list[tuple[str, str]]:
    """Assemble the tracer tags reported to the agent (git metadata, env, version, host)."""
    tags = ddtrace.config.tags.copy()
    gitmetadata.add_tags(tags)
    if ddtrace.config.env:
        tags["env"] = ddtrace.config.env
    if ddtrace.config.version:
        tags["version"] = ddtrace.config.version
    tags["tracer_version"] = tracer_version
    tags["host_name"] = get_hostname()
    return [(str(k), str(v)) for k, v in tags.items()]


def _build_process_tags() -> list[tuple[str, str]]:
    pairs: list[tuple[str, str]] = []
    for tag in process_tags.process_tags_list or []:
        key, _, value = tag.partition(":")
        pairs.append((key, value))
    return pairs


class RemoteConfigClient:
    """Adapter over the native (libdatadog) Remote Configuration client."""

    def __init__(self) -> None:
        self.id = str(uuid.uuid4())
        self.agent_url = agent_config.trace_agent_url

        # Product callbacks for single subscriber architecture
        self._product_callbacks: "dict[RemoteConfigProduct, RCCallback]" = {}
        # Track which products are enabled (reported to the agent each poll)
        self._enabled_products: "set[RemoteConfigProduct]" = set()
        self._capability_values: list = []

        # Native client (created lazily on the master process) and the
        # native reader (created in forked children to consume SHM).
        self._native: Optional[Any] = None
        self._reader: Optional[Any] = None

    def _ensure_native(self) -> Any:
        if self._native is None:
            from ddtrace.internal.native import RemoteConfigClient as _NativeClient
            from ddtrace.internal.native_runtime import get_native_runtime

            tracer_version = _pep440_to_semver()
            self._native = _NativeClient(
                get_native_runtime(),
                agent_url=str(self.agent_url),
                tracer_version=tracer_version,
                client_id=self.id,
                runtime_id=runtime.get_runtime_id(),
                service=ddtrace.config.service or "",
                env=ddtrace.config.env or "",
                app_version=ddtrace.config.version or "",
                tags=_build_tags(tracer_version),
                process_tags=_build_process_tags(),
            )
            if self._capability_values:
                self._native.add_capabilities(self._capability_values)
        return self._native

    def renew_id(self) -> None:
        self.id = str(uuid.uuid4())

    def register_callback(self, product_name: "RemoteConfigProduct", callback: RCCallback) -> None:
        self._product_callbacks[product_name] = callback
        log.debug("[%s][P: %s] Registered callback for product %s", os.getpid(), os.getppid(), product_name)

    def enabled_product_names(self) -> list:
        return [str(p) for p in self._enabled_products]

    def unregister_product(self, product_name: "RemoteConfigProduct") -> None:
        self._product_callbacks.pop(product_name, None)
        log.debug("[%s][P: %s] Unregistered product %s", os.getpid(), os.getppid(), product_name)

    def update_product_callback(self, product_name: "RemoteConfigProduct", callback: RCCallback) -> bool:
        if product_name in self._product_callbacks:
            self._product_callbacks[product_name] = callback
            log.debug("[%s][P: %s] Updated callback for product %s", os.getpid(), os.getppid(), product_name)
            return True
        return False

    def enable_product(self, product_name: "RemoteConfigProduct") -> None:
        self._enabled_products.add(product_name)
        log.debug("[%s][P: %s] Enabled product %s", os.getpid(), os.getppid(), product_name)

    def disable_product(self, product_name: "RemoteConfigProduct") -> None:
        self._enabled_products.discard(product_name)
        log.debug("[%s][P: %s] Disabled product %s", os.getpid(), os.getppid(), product_name)

    def add_capabilities(self, capabilities) -> None:
        # `capabilities` are native RemoteConfigCapabilities values; buffer them
        # (so they survive lazy native creation) and forward to the native client,
        # which does the accumulation/encoding.
        caps = list(capabilities)
        if not caps:
            return
        self._capability_values.extend(caps)
        if self._native is not None:
            self._native.add_capabilities(caps)

    def reset_products(self) -> None:
        self._product_callbacks = dict()
        self._enabled_products = set()

    def _build_payloads(self, changes: Sequence[Any]) -> dict[Any, list[Payload]]:
        grouped: dict[Any, list[Payload]] = {}
        for change in changes:
            raw = change.content
            content: PayloadType = None
            if raw is not None:
                try:
                    content = json.loads(raw)
                except Exception:
                    log.debug("invalid remote config JSON content for %s", change.path, exc_info=True)
                    if self._native is not None:
                        try:
                            self._native.set_config_state(change.path, "invalid JSON content")
                        except Exception:
                            log.debug("failed to report config error for %s", change.path, exc_info=True)
                    continue
            metadata = ConfigMetadata(
                id=change.config_id,
                product_name=str(change.product),
                sha256_hash=None,
                length=len(raw) if raw is not None else None,
                tuf_version=change.version,
            )
            grouped.setdefault(change.product, []).append(Payload(metadata, change.path, content))
        return grouped

    def _dispatch_to_products(self, grouped: dict[Any, list[Payload]]) -> None:
        # Copy the callbacks so registration/unregistration during dispatch is safe.
        product_callbacks = self._product_callbacks.copy()

        for product, callback in product_callbacks.items():
            try:
                with StopWatch() as sw:
                    callback.periodic()

                if (elapsed_time := sw.elapsed()) > CALLBACK_EXECUTION_WARNING_THRESHOLD:
                    telemetry_writer.add_log(
                        TELEMETRY_LOG_LEVEL.WARNING,
                        "Periodic RC operation exceeded threshold",
                        tags={
                            "product": str(product),
                            "callback_type": "periodic",
                            "elapsed_time": "%.3f" % elapsed_time,
                        },
                    )
            except Exception:
                log.error(
                    "[%s][P: %s] Error calling periodic method for product %s",
                    os.getpid(),
                    os.getppid(),
                    product,
                    exc_info=True,
                )

        for product, product_payload_list in grouped.items():
            product_callback = product_callbacks.get(product)
            if product_callback is not None:
                try:
                    log.debug(
                        "[%s][P: %s] Dispatching %d payloads to product %s",
                        os.getpid(),
                        os.getppid(),
                        len(product_payload_list),
                        product,
                    )
                    with StopWatch() as sw:
                        product_callback(product_payload_list)

                    if (elapsed_time := sw.elapsed()) > CALLBACK_EXECUTION_WARNING_THRESHOLD:
                        telemetry_writer.add_log(
                            TELEMETRY_LOG_LEVEL.WARNING,
                            "RC callback operation exceeded threshold",
                            tags={
                                "product": str(product),
                                "callback_type": "payload",
                                "elapsed_time": "%.3f" % elapsed_time,
                            },
                        )
                except Exception:
                    log.error(
                        "[%s][P: %s] Error dispatching to product %s. Payloads: %r",
                        os.getpid(),
                        os.getppid(),
                        product,
                        product_payload_list,
                        exc_info=True,
                    )

    def dispatch_native_changes(self, records: Sequence[Any]) -> None:
        """Build payloads from native change records and dispatch them.

        Used by the child-process subscriber: a bare poll tick (``records`` is
        empty) still fires every callback's ``periodic()`` via an empty dispatch.
        """
        self._dispatch_to_products(self._build_payloads(records) if records else {})

    def request(self) -> bool:
        try:
            native = self._ensure_native()
            changes = native.poll(
                list(self._enabled_products),
                list(ddtrace.config._get_extra_services()),
            )
            self._dispatch_to_products(self._build_payloads(changes))
            return True
        except Exception:
            log.debug("remote configuration client request failed", exc_info=True)
            return False

    def enable_shared_memory(self) -> None:
        """Enable cross-process SHM on the master process (called before forking)."""
        try:
            self._ensure_native().enable_shared_memory()
        except Exception:
            log.debug("failed to enable remote config broadcast", exc_info=True)

    def make_reader(self) -> Optional[Any]:
        """Create a native reader over the inherited shared memory or reuse an existing one."""
        if self._reader is not None:
            # Reused across a fork-of-a-fork: the mapped SHM segments survive the fork,
            # only the memoized data must be cleared so the full snapshot is re-emitted here.
            try:
                self._reader.reset()
            except Exception:
                log.debug("failed to reset inherited remote config reader", exc_info=True)
                self._reader = None
            return self._reader

        if self._native is None:
            return None
        try:
            self._reader = self._native.make_reader()
        except Exception:
            log.debug("failed to create remote config reader", exc_info=True)
            self._reader = None
        return self._reader
