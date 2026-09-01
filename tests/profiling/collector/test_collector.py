from contextlib import contextmanager
import importlib
import sys
import types
from typing import Iterator

import pytest

from ddtrace.profiling import collector


def _test_repr(collector_class: type[collector.Collector], s: str) -> None:
    assert repr(collector_class()) == s


def test_capture_sampler() -> None:
    cs: collector.CaptureSampler = collector.CaptureSampler(15)
    assert cs.capture() is False  # 15
    assert cs.capture() is False  # 30
    assert cs.capture() is False  # 45
    assert cs.capture() is False  # 60
    assert cs.capture() is False  # 75
    assert cs.capture() is False  # 90
    assert cs.capture() is True  # 5
    assert cs.capture() is False  # 20
    assert cs.capture() is False  # 35
    assert cs.capture() is False  # 50
    assert cs.capture() is False  # 65
    assert cs.capture() is False  # 80
    assert cs.capture() is False  # 95
    assert cs.capture() is True  # 10
    assert cs.capture() is False  # 25
    assert cs.capture() is False  # 40
    assert cs.capture() is False  # 55
    assert cs.capture() is False  # 70
    assert cs.capture() is False  # 85
    assert cs.capture() is True  # 0
    assert cs.capture() is False  # 15


def test_capture_sampler_bad_value() -> None:
    with pytest.raises(ValueError):
        collector.CaptureSampler(-1)

    with pytest.raises(ValueError):
        collector.CaptureSampler(102)


def test_capture_sampler_pure_python_fallback() -> None:
    """CaptureSampler must remain importable when the Cython _sampler extension is absent (DD_CYTHONIZE=0)."""
    mod_name: str = "ddtrace.profiling.collector._sampler"
    collector_mod: str = "ddtrace.profiling.collector"

    saved_module: types.ModuleType | None = sys.modules.pop(mod_name, None)
    sys.modules.pop(collector_mod, None)

    sys.modules[mod_name] = None  # type: ignore[assignment]  # block the import
    try:
        mod: types.ModuleType = importlib.import_module(collector_mod)
        cs: collector.CaptureSampler = mod.CaptureSampler(50)
        assert cs.capture() is False  # 50
        assert cs.capture() is True  # 0
        assert repr(cs) == "CaptureSampler(capture_pct=50)"
        with pytest.raises(ValueError):
            mod.CaptureSampler(-1)
    finally:
        del sys.modules[mod_name]
        if saved_module is not None:
            sys.modules[mod_name] = saved_module
        sys.modules.pop(collector_mod, None)
        importlib.import_module(collector_mod)


@contextmanager
def _collector_wrapper_without_extension(wrapper_mod: str, extension_mod: str) -> Iterator[types.ModuleType]:
    """Re-import a collector wrapper while its Cython extension is missing (ImportError path)."""
    saved_ext: types.ModuleType | None = sys.modules.pop(extension_mod, None)
    saved_wrapper: types.ModuleType | None = sys.modules.pop(wrapper_mod, None)
    sys.modules[extension_mod] = None  # type: ignore[assignment]
    try:
        yield importlib.import_module(wrapper_mod)
    finally:
        del sys.modules[extension_mod]
        if saved_ext is not None:
            sys.modules[extension_mod] = saved_ext
        sys.modules.pop(wrapper_mod, None)
        if saved_wrapper is not None:
            sys.modules[wrapper_mod] = saved_wrapper
        else:
            importlib.import_module(wrapper_mod)


@pytest.mark.parametrize(
    ("wrapper_mod", "extension_mod", "class_name", "ctor_kwargs"),
    (
        (
            "ddtrace.profiling.collector.threading",
            "ddtrace.profiling.collector._lock",
            "ThreadingLockCollector",
            {"tracer": None},
        ),
        (
            "ddtrace.profiling.collector.asyncio",
            "ddtrace.profiling.collector._lock",
            "AsyncioLockCollector",
            {"tracer": None},
        ),
        (
            "ddtrace.profiling.collector.exception",
            "ddtrace.profiling.collector._exception",
            "ExceptionCollector",
            {},
        ),
    ),
)
def test_collector_stubs_construct_and_raise_unavailable(
    wrapper_mod: str,
    extension_mod: str,
    class_name: str,
    ctor_kwargs: dict[str, object],
) -> None:
    """ImportError of the Cython extension must yield a constructible CollectorUnavailable stub.

    profiler.py instantiates lock collectors as ``Collector(tracer=...)`` outside the
    try that catches CollectorUnavailable; stubs that cannot be constructed crash
    the profiler instead of being skipped.
    """
    with _collector_wrapper_without_extension(wrapper_mod, extension_mod) as mod:
        stub_cls: type[collector.Collector] = getattr(mod, class_name)
        col: collector.Collector = stub_cls(**ctor_kwargs)
        # Use the live module: earlier tests may have re-imported
        # ddtrace.profiling.collector, so the test-module binding can be stale.
        live_collector: types.ModuleType = sys.modules["ddtrace.profiling.collector"]
        unavailable_cls: type[BaseException] = live_collector.CollectorUnavailable
        with pytest.raises(unavailable_cls):
            col.start()
