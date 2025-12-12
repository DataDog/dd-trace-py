# Instrumentation API v2 - Design Document

## Overview

**Ultra-minimal instrumentation side** - just wrap and emit raw objects. The plugin handles all extraction and tracing.

```
┌─────────────────────────────────────┐     ┌─────────────────────────────────────┐
│         INSTRUMENTOR                 │     │           PLUGIN                    │
│   (dumb wrapper)                     │     │   (all the smarts)                  │
├─────────────────────────────────────┤     ├─────────────────────────────────────┤
│ • Wraps library functions            │     │ • Extracts connection info          │
│ • Passes (instance, args, kwargs)    │────▶│ • Extracts query/request data       │
│ • Knows supported versions           │     │ • Creates spans                     │
│ • That's it!                         │     │ • Sets tags, handles errors         │
└─────────────────────────────────────┘     └─────────────────────────────────────┘
```

---

## Instrumentor API

### Base Class

```python
# ddtrace/contrib/auto/_base.py

from abc import ABC, abstractmethod
from typing import Dict, List, Tuple
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
        Pin(_config=config[self.package]).onto(module)

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
            with core.context_with_data(
                event_name,
                pin=pin,
                instance=instance,
                args=args,
                kwargs=kwargs,
                call=wrapped,
            ) as ctx, ctx.span:
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
            with core.context_with_data(
                event_name,
                pin=pin,
                instance=instance,
                args=args,
                kwargs=kwargs,
                call=wrapped,
            ) as ctx, ctx.span:
                return await wrapped(*args, **kwargs)

        return wrapper
```

---

## Example: asyncpg Instrumentor

The instrumentor is now trivially simple:

```python
# ddtrace/contrib/auto/asyncpg/__init__.py

from ddtrace.contrib.auto._base import Instrumentor


class AsyncpgInstrumentor(Instrumentor):
    """asyncpg instrumentation."""

    package = "asyncpg"

    supported_versions = {"asyncpg": ">=0.23.0"}

    methods_to_wrap = [
        ("protocol.Protocol.execute", "execute"),
        ("protocol.Protocol.bind_execute", "execute"),
        ("protocol.Protocol.query", "execute"),
        ("protocol.Protocol.bind_execute_many", "execute"),
    ]

    default_config = {
        "_default_service": "postgres",
    }

    def _make_wrapper(self, operation):
        # Override to use async wrapper
        return self._make_async_wrapper(operation)


# Module-level API
_instrumentor = AsyncpgInstrumentor()


def patch():
    _instrumentor.instrument()


def unpatch():
    _instrumentor.uninstrument()


def get_versions():
    return _instrumentor.supported_versions
```

**That's ~25 lines for the entire instrumentor!**

---

## Updated Plugin (extracts from raw objects)

The plugin now does all the extraction:

```python
# ddtrace/_trace/tracing_plugins/contrib/asyncpg.py

from typing import Any, Dict, Optional, Tuple
from ddtrace._trace.tracing_plugins.base.database import DatabasePlugin


class AsyncpgExecutePlugin(DatabasePlugin):
    """
    Handles asyncpg.execute events.

    Extracts connection info and query from raw instance/args.
    """

    @property
    def package(self) -> str:
        return "asyncpg"

    @property
    def operation(self) -> str:
        return "execute"

    system = "postgresql"
    db_system = "postgresql"

    def on_start(self, ctx) -> None:
        """Extract data from raw objects and create span."""
        from ddtrace._trace.tracing_plugins.base.events import DatabaseContext

        pin = ctx.get_item("pin")
        if not pin or not pin.enabled():
            return

        # Extract from raw objects passed by instrumentor
        instance = ctx.get_item("instance")
        args = ctx.get_item("args", ())
        kwargs = ctx.get_item("kwargs", {})

        # Extract query
        query = self._extract_query(args, kwargs)

        # Extract connection info from protocol instance
        conn_info = self._extract_connection_info(instance)

        # Build context and call parent
        db_ctx = DatabaseContext(
            db_system=self.db_system,
            query=query,
            host=conn_info.get("host"),
            port=conn_info.get("port"),
            user=conn_info.get("user"),
            database=conn_info.get("database"),
        )

        ctx.set_item("db_context", db_ctx)
        ctx.set_item("resource", query)

        # Let parent create span
        super().on_start(ctx)

    def _extract_query(self, args: tuple, kwargs: dict) -> Optional[str]:
        """Extract query from execute arguments."""
        state = args[0] if args else kwargs.get("state")
        if isinstance(state, (str, bytes)):
            return state if isinstance(state, str) else state.decode("utf-8", errors="replace")
        # PreparedStatement
        return getattr(state, "query", None)

    def _extract_connection_info(self, instance) -> Dict[str, Any]:
        """Extract connection info from Protocol instance."""
        conn = getattr(instance, "_connection", None)
        if not conn:
            return {}

        addr = getattr(conn, "_addr", None)
        params = getattr(conn, "_params", None)

        result = {}
        if addr and isinstance(addr, tuple) and len(addr) >= 2:
            result["host"] = addr[0]
            result["port"] = addr[1]
        if params:
            result["user"] = getattr(params, "user", None)
            result["database"] = getattr(params, "database", None)

        return result
```

---

## Benefits of This Approach

| Aspect | Benefit |
|--------|---------|
| **Instrumentor simplicity** | Just a list of methods to wrap |
| **No duplication** | Extraction logic only in plugin |
| **Testable** | Plugin can be tested with mock objects |
| **Flexible** | Plugin can extract different data for different needs |
| **Library-agnostic instrumentor** | Almost boilerplate |

---

## Directory Structure

```
ddtrace/
├── contrib/
│   └── auto/
│       ├── __init__.py
│       ├── _base.py              # Instrumentor base class (~80 lines)
│       │
│       ├── asyncpg/
│       │   └── __init__.py       # ~25 lines
│       ├── psycopg/
│       │   └── __init__.py       # ~25 lines
│       ├── httpx/
│       │   └── __init__.py       # ~25 lines
│       └── kafka/
│           └── __init__.py       # ~30 lines
│
└── _trace/
    └── tracing_plugins/
        ├── base/                  # Base plugins (already implemented)
        └── contrib/
            ├── asyncpg.py        # Extraction + tracing logic
            ├── psycopg.py
            ├── httpx.py
            └── kafka.py
```

---

## Flow Summary

```
1. User calls: await conn.execute("SELECT * FROM users")

2. Instrumentor wrapper:
   - Gets pin
   - Emits: core.context_with_data("asyncpg.execute",
            instance=protocol, args=("SELECT...",), kwargs={})

3. Plugin.on_start():
   - Extracts query from args[0]
   - Extracts host/port/user/db from instance._connection
   - Creates DatabaseContext
   - Calls super().on_start() → creates span with tags

4. Original method runs

5. Plugin.on_finish():
   - Sets rowcount if available
   - Handles errors
   - Finishes span
```

---

## Comparison

| | Old Pattern | New Instrumentor | New Plugin |
|---|-------------|------------------|------------|
| Lines of code | ~200 | ~25 | ~50 |
| Extracts data | Yes | No | Yes |
| Creates spans | Yes | No | Yes |
| Handles errors | Yes | No | Yes |
| Library knowledge | Yes | Minimal (just method names) | Yes |
| Tracing knowledge | Yes | No | Yes |
