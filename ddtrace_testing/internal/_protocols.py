"""Protocols for testing.internal types, to break circular imports.

These protocols allow modules to depend on the interface rather than the
concrete implementation, avoiding circular imports between testing modules.
"""

import typing as t


class TestRunProtocol(t.Protocol):
    """Protocol for TestRun, to avoid a circular import between telemetry.py and test_data.py."""

    test: t.Any

    def is_benchmark(self) -> bool: ...

    def is_retry(self) -> bool: ...

    def is_rum(self) -> bool: ...

    def get_browser_driver(self) -> t.Optional[str]: ...

    def has_failed_all_retries(self) -> bool: ...


class BackendConnectorSetupProtocol(t.Protocol):
    """Protocol for BackendConnectorSetup, to avoid a circular import between http.py and telemetry.py."""

    def get_connector_for_subdomain(self, subdomain: t.Any) -> t.Any: ...

    def default_env(self) -> str: ...
