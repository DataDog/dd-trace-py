"""
Instrumentor base class for the new instrumentation API v2.

Instrumentors are DUMB - they only:
1. Wrap library functions
2. Emit events with raw (instance, args, kwargs)
3. Know supported versions

All extraction and tracing logic lives in plugins.
"""

from abc import ABC
from abc import abstractmethod
from typing import Dict
from typing import List
from typing import Tuple

from ddtrace import config
from ddtrace._trace.pin import Pin
from ddtrace.internal import core


class Instrumentor(ABC):
    """
    Base class for instrumentors.

    Instrumentors are DUMB - they only:
    1. Wrap library functions
    2. Emit events with raw (instance, args, kwargs)
    3. Know supported versions

    All extraction and tracing logic lives in plugins.
    """

    # --- Required ---

    @property
    @abstractmethod
    def package(self) -> str:
        """Package name (e.g., 'asyncpg')."""
        pass

    @property
    @abstractmethod
    def supported_versions(self) -> Dict[str, str]:
        """
        Supported package versions.

        Returns:
            Dict mapping package name to version spec.
            Example: {"asyncpg": ">=0.23.0"}
        """
        pass

    @property
    @abstractmethod
    def methods_to_wrap(self) -> List[Tuple[str, str]]:
        """
        Methods to wrap.

        Returns:
            List of (target, operation) tuples.
            target: Dot-path to method (e.g., "protocol.Protocol.execute")
            operation: Event operation name (e.g., "execute")

        Example:
            [
                ("protocol.Protocol.execute", "execute"),
                ("protocol.Protocol.query", "query"),
                ("connect", "connect"),
            ]
        """
        pass

    # --- Optional config ---

    @property
    def default_config(self) -> Dict:
        """Default config values."""
        return {}

    # --- State ---

    _is_instrumented: bool = False

    # --- Public API ---

    def instrument(self) -> None:
        """Instrument the library."""
        if self._is_instrumented:
            return

        module = self._import_module()
        if module is None:
            return

        self._register_config()
        Pin(_config=config._get_config(self.package)).onto(module)

        for target, operation in self.methods_to_wrap:
            self._wrap_method(module, target, operation)

        module._datadog_patch = True
        self._is_instrumented = True

    def uninstrument(self) -> None:
        """Remove instrumentation."""
        if not self._is_instrumented:
            return

        module = self._import_module()
        if module is None:
            return

        for target, _ in self.methods_to_wrap:
            self._unwrap_method(module, target)

        module._datadog_patch = False
        self._is_instrumented = False

    # --- Internal ---

    def _import_module(self):
        try:
            from importlib import import_module

            return import_module(self.package)
        except ImportError:
            return None

    def _register_config(self):
        if not hasattr(config, self.package):
            config._add(self.package, self.default_config)

    def _wrap_method(self, module, target: str, operation: str):
        """Wrap a method to emit events."""
        from wrapt import wrap_function_wrapper

        parts = target.split(".")
        if len(parts) == 1:
            # Top-level function
            wrap_function_wrapper(module, target, self._make_wrapper(operation))
        else:
            # Nested: module.Class.method or module.submodule.Class.method
            obj = module
            for part in parts[:-1]:
                obj = getattr(obj, part)
            wrap_function_wrapper(obj, parts[-1], self._make_wrapper(operation))

    def _unwrap_method(self, module, target: str):
        from ddtrace.contrib.internal.trace_utils import unwrap

        parts = target.split(".")
        if len(parts) == 1:
            unwrap(module, target)
        else:
            obj = module
            for part in parts[:-1]:
                obj = getattr(obj, part)
            unwrap(obj, parts[-1])

    def _make_wrapper(self, operation: str):
        """Create wrapper that emits event with raw objects."""
        pkg = self.package

        def wrapper(wrapped, instance, args, kwargs):
            pin = Pin.get_from(instance) or Pin.get_from(self._import_module())
            if not pin or not pin.enabled():
                return wrapped(*args, **kwargs)

            # Emit event with raw objects - plugin extracts what it needs
            event_name = f"{pkg}.{operation}"
            with (
                core.context_with_data(
                    event_name,
                    pin=pin,
                    instance=instance,
                    args=args,
                    kwargs=kwargs,
                    call=wrapped,
                ) as ctx,
                ctx.span,
            ):
                return wrapped(*args, **kwargs)

        return wrapper

    def _make_async_wrapper(self, operation: str):
        """Create async wrapper."""
        pkg = self.package

        async def wrapper(wrapped, instance, args, kwargs):
            pin = Pin.get_from(instance) or Pin.get_from(self._import_module())
            if not pin or not pin.enabled():
                return await wrapped(*args, **kwargs)

            event_name = f"{pkg}.{operation}"
            with (
                core.context_with_data(
                    event_name,
                    pin=pin,
                    instance=instance,
                    args=args,
                    kwargs=kwargs,
                    call=wrapped,
                ) as ctx,
                ctx.span,
            ):
                return await wrapped(*args, **kwargs)

        return wrapper
