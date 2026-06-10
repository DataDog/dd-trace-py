import importlib
import sys
import types

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
        # Re-attach any already-imported direct submodules as attributes of the
        # freshly-reimported package.  Python only does this automatically on a
        # fresh import of the submodule itself; it does not update attributes on
        # a parent package object that was replaced in sys.modules mid-flight.
        _parent = sys.modules[collector_mod]
        _prefix = collector_mod + "."
        _depth = collector_mod.count(".") + 1
        for _key in list(sys.modules):
            if _key.startswith(_prefix) and _key.count(".") == _depth:
                _attr = _key.rsplit(".", 1)[-1]
                if not hasattr(_parent, _attr):
                    setattr(_parent, _attr, sys.modules[_key])
