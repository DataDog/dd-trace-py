"""Lets products react to an integration being patched without the integration importing them.

A product registers a module under the ddtrace.integration_hooks entry-point group, keyed
by integration name, that provides listen() and unlisten(). Integrations call on_patch and
on_unpatch so the hooks run however the integration was patched, including a direct patch()
call where no product lifecycle runs.
"""

from collections import defaultdict
import typing as t
from typing import Protocol

from ddtrace.internal.entry_points import get_entry_points
from ddtrace.internal.entry_points import is_trusted
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)

GROUP = "ddtrace.integration_hooks"


class IntegrationHooks(Protocol):
    def listen(self) -> None: ...

    def unlisten(self) -> None: ...


_entry_points: t.Optional[dict[str, list[t.Any]]] = None
_loaded: dict[str, list[IntegrationHooks]] = {}


def _hooks_for(integration: str) -> list[IntegrationHooks]:
    global _entry_points

    if _entry_points is None:
        _entry_points = defaultdict(list)
        for plugin in get_entry_points(GROUP):
            if is_trusted(plugin):
                _entry_points[plugin.name].append(plugin)

    # Hook modules are only imported once their integration is patched, so applications
    # that never patch it don't pay for importing the product.
    hooks = _loaded.get(integration)
    if hooks is None:
        hooks = []
        for plugin in _entry_points.get(integration, ()):
            try:
                hooks.append(plugin.load())
            except Exception:
                log.error("Failed to load integration hook '%s' (%s)", integration, plugin.value, exc_info=True)
        _loaded[integration] = hooks
    return hooks


def on_patch(integration: str) -> None:
    for hooks in _hooks_for(integration):
        try:
            hooks.listen()
        except Exception:
            log.error("Integration hook for '%s' failed on patch", integration, exc_info=True)


def on_unpatch(integration: str) -> None:
    for hooks in _hooks_for(integration):
        try:
            hooks.unlisten()
        except Exception:
            log.error("Integration hook for '%s' failed on unpatch", integration, exc_info=True)
