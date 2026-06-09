"""Regression tests for PyTorch integration edge cases:

* ``install()`` / ``uninstall()`` must be idempotent (no wrapper stacking).
* A full patch / unpatch / patch cycle must leave exactly one wrapper layer.
* Exceptions raised inside ``_bootstrap_distributed`` / ``_wrapped_destroy_process_group``
  must not leave the integration in a broken state.
"""

import pytest
import torch

from ddtrace.contrib.internal.pytorch import _distributed
from ddtrace.contrib.internal.pytorch import _test_helpers as _th
from ddtrace.contrib.internal.pytorch import patch as pytorch_patch


def _force_clean_wraps() -> None:
    """Defensively remove any pytorch wraps left by earlier tests in this
    session. Earlier tests (e.g. ``test_layer_one_gating``) call
    ``_distributed.install()`` directly, bypassing the
    ``torch._datadog_patch`` flag, so the high-level ``unpatch()`` returns
    early and leaves wrappers attached. Force ``_installed = True`` and call
    ``uninstall()`` to walk the canonical teardown path.
    """
    setattr(torch, "_datadog_patch", False)
    _distributed._installed = True
    try:
        _distributed.uninstall()
    except Exception:
        pass


@pytest.fixture
def _clean_state(monkeypatch):
    _force_clean_wraps()
    _th.reset_device_cache()
    _th.close_rank_root()
    yield
    _force_clean_wraps()
    _th.reset_device_cache()
    _th.close_rank_root()


def _dd_wrapper_depth(fn) -> int:
    """Count only ``wrapt``-added layers.

    torch itself decorates some distributed functions with ``functools.wraps``,
    which also sets ``__wrapped__``; we only count layers that are ``wrapt``
    ``FunctionWrapper`` instances so torch's own decorators are excluded.
    """
    import wrapt

    depth = 0
    f = fn
    while isinstance(f, wrapt.FunctionWrapper):
        depth += 1
        f = f.__wrapped__
    return depth


def _dd_wrapper_depth_ipg() -> int:
    """Wrapper depth on ``torch.distributed.init_process_group``.

    The integration wraps ``init_process_group`` and ``destroy_process_group`` (not
    collectives), so we measure idempotency on those two functions.
    """
    return _dd_wrapper_depth(torch.distributed.init_process_group)


def test_install_is_idempotent_no_wrapper_stacking(_clean_state):
    """Calling install() twice must not stack wrappers on torch.distributed."""
    assert _dd_wrapper_depth_ipg() == 0
    _distributed.install()
    depth_after_first = _dd_wrapper_depth_ipg()
    assert depth_after_first == 1
    _distributed.install()  # must be a no-op
    assert _dd_wrapper_depth_ipg() == depth_after_first
    _distributed.uninstall()
    assert _dd_wrapper_depth_ipg() == 0


def test_patch_unpatch_patch_cycle_is_clean(_clean_state):
    """A full patch/unpatch/patch cycle must leave exactly one wrapper layer."""
    pytorch_patch.patch()
    depth_after_first = _dd_wrapper_depth_ipg()
    pytorch_patch.unpatch()
    assert _dd_wrapper_depth_ipg() == 0
    pytorch_patch.patch()
    assert _dd_wrapper_depth_ipg() == depth_after_first


def test_uninstall_is_idempotent(_clean_state):
    """uninstall() without a prior install() is a no-op."""
    _distributed.uninstall()
    _distributed.uninstall()


def test_exception_in_bootstrap_does_not_corrupt_install_state(monkeypatch, _clean_state):
    """If _bootstrap_distributed raises, install() state is still usable."""
    monkeypatch.setattr(_distributed, "_bootstrap_distributed", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    pytorch_patch.patch()
    # init_process_group wrapper is in place even after a bootstrap failure
    assert _distributed._installed
    pytorch_patch.unpatch()
    assert not _distributed._installed


def test_exception_in_destroy_still_closes_rank_span(monkeypatch, _clean_state):
    """_rank_root.close() is called even when destroy_process_group raises."""
    closed = []
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._rank_root.close",
        lambda: closed.append(True),
    )

    def raising_destroy(*a, **kw):
        raise RuntimeError("destroy failed")

    monkeypatch.setattr(torch.distributed, "destroy_process_group", raising_destroy)
    pytorch_patch.patch()
    with pytest.raises(RuntimeError, match="destroy failed"):
        torch.distributed.destroy_process_group()
    assert closed, "_rank_root.close() was not called despite try/finally"
    pytorch_patch.unpatch()
