from __future__ import annotations

import typing as t

from ddtrace.testing.internal.session_manager import SessionManager


class MainPluginProtocol(t.Protocol):
    """Structural type for the subset of TestOptPlugin used by framework-specific sub-plugins (e.g. BddTestOptPlugin).

    Defined here (rather than importing TestOptPlugin from plugin.py directly) so that plugin.py can import
    sub-plugins like bdd.py without creating an import cycle.
    """

    manager: SessionManager
