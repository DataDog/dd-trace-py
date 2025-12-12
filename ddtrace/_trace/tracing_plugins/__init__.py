"""
Integration tracing plugins.

This module provides the plugin system for tracing integrations.

The plugin system uses a hierarchical structure:

    TracingPlugin (base)
    ├── OutboundPlugin (TO external services)
    │   ├── ClientPlugin (HTTP clients, etc.)
    │   │   └── StoragePlugin (storage systems)
    │   │       └── DatabasePlugin (databases)
    │   └── ProducerPlugin (message producers)
    └── InboundPlugin (FROM external sources)
        ├── ServerPlugin (HTTP servers, etc.)
        └── ConsumerPlugin (message consumers)

Usage:
    # During ddtrace initialization
    from ddtrace._trace.tracing_plugins import initialize

    initialize()  # Registers all plugins

Adding a new integration:
    1. Create a plugin class in contrib/ that extends the appropriate base
    2. Define `package` and `operation` properties
    3. Register it in this module's initialize() function
"""

from typing import List
from typing import Type

from ddtrace._trace.tracing_plugins.base.tracing import TracingPlugin
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

# All registered plugins
_plugins: List[TracingPlugin] = []
_initialized: bool = False


def register(plugin_cls: Type[TracingPlugin]) -> None:
    """
    Register a plugin class.

    Creates an instance and registers its event handlers.

    Args:
        plugin_cls: Plugin class to register
    """
    try:
        plugin = plugin_cls()
        plugin.register()
        _plugins.append(plugin)
        log.debug("Registered tracing plugin: %s.%s", plugin.package, plugin.operation)
    except Exception:
        log.warning(
            "Failed to register tracing plugin: %s",
            plugin_cls.__name__,
            exc_info=True,
        )


def initialize() -> None:
    """
    Initialize all v2 integration plugins.

    This should be called during ddtrace initialization.
    Safe to call multiple times - only initializes once.
    """
    global _initialized

    if _initialized:
        return

    _initialized = True

    # Import contrib plugins
    from ddtrace._trace.tracing_plugins.contrib import asyncpg

    # Database plugins
    register(asyncpg.AsyncpgExecutePlugin)
    register(asyncpg.AsyncpgConnectPlugin)

    # TODO: Add more plugins as integrations are migrated:
    # from ddtrace._trace.tracing_plugins.contrib import psycopg
    # from ddtrace._trace.tracing_plugins.contrib import mysql
    # from ddtrace._trace.tracing_plugins.contrib import httpx
    # from ddtrace._trace.tracing_plugins.contrib import flask
    # from ddtrace._trace.tracing_plugins.contrib import kafka

    log.debug("Initialized %d tracing plugins", len(_plugins))


def get_plugins() -> List[TracingPlugin]:
    """
    Get all registered plugins.

    Returns:
        List of registered plugin instances
    """
    return _plugins.copy()


def reset() -> None:
    """
    Reset plugin registration.

    This is primarily for testing purposes.
    """
    global _plugins, _initialized
    _plugins = []
    _initialized = False
