from ddtrace.internal.settings._core import DDConfig
from ddtrace.internal.settings._core import field
from ddtrace.internal.settings._registry import Config


def test_include_nesting_exposes_namespaced_fields():
    # A product config nested under a namespace is reachable as root.<ns>.<field>,
    # with the field resolving its registry-backed type/default.
    class _Root(DDConfig):
        pass

    class _Prod(DDConfig):
        __prefix__ = "dd.dynamic_instrumentation"
        enabled = field()

    _Root.include(_Prod, namespace="prod")
    cfg = _Root()
    assert cfg.prod.enabled is False


def test_nested_field_reads_environment(monkeypatch):
    monkeypatch.setenv("DD_DYNAMIC_INSTRUMENTATION_ENABLED", "true")

    class _Root(DDConfig):
        pass

    class _Prod(DDConfig):
        __prefix__ = "dd.dynamic_instrumentation"
        enabled = field()

    _Root.include(_Prod, namespace="prod")
    assert _Root().prod.enabled is True


def test_get_returns_singleton():
    assert Config.get() is Config.get()
