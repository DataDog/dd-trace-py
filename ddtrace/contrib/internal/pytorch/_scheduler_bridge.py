"""Wraps every torch LR-scheduler ``step`` so dd-trace-c sees LR
changes. Single-key bridge updates would clobber the rank-span context
(the C side overwrites, not merges), so the LR is staged into
``_rank_root._open_kwargs`` and the full bundle is republished there.

Three classes are wrapped because they don't share a base:
``LRScheduler`` (torch ≥ 2.0), ``_LRScheduler`` (legacy alias), and
``ReduceLROnPlateau``.
"""

from __future__ import annotations

from typing import Any
from typing import List
from typing import Optional
from typing import Tuple

import wrapt

from ddtrace.contrib.internal.pytorch import _rank_root
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger


log = get_logger(__name__)
_INSTALLED = False
_INSTALLED_WRAPS: List[Tuple[Any, str]] = []
# Skip the C call when LR hasn't changed — epoch-granularity schedulers
# in steady state fire step() with no actual change every iter.
_last_lr: Optional[float] = None


def _reset_child_state() -> None:
    """Forked child must re-install (parent's wrap survived the fork
    because torch is shared, but our `_INSTALLED` flag is stale) and
    must NOT suppress the first LR change just because it happens to
    match the parent's last LR.
    """
    global _INSTALLED, _INSTALLED_WRAPS, _last_lr
    _INSTALLED = False
    _INSTALLED_WRAPS = []
    _last_lr = None


forksafe.register(_reset_child_state)

_CANDIDATES = ("LRScheduler", "_LRScheduler", "ReduceLROnPlateau")


def _import_lr_scheduler() -> Any:
    import torch.optim.lr_scheduler as m

    return m


def _on_step_post(scheduler: Any) -> None:
    global _last_lr
    try:
        opt = getattr(scheduler, "optimizer", None)
        if opt is None:
            return
        pgs = getattr(opt, "param_groups", None)
        if not pgs:
            return
        lr = pgs[0].get("lr")
        if lr is None:
            return
        lr = float(lr)
        if _last_lr is not None and lr == _last_lr:
            return
        _last_lr = lr
        _rank_root.republish_with_extra_kwarg("optim.current_learning_rate", lr)
    except Exception:
        log.debug("pytorch._scheduler_bridge: on-step post hook failed", exc_info=True)


def _wrapped_step(wrapped, instance, args, kwargs):
    rv = wrapped(*args, **kwargs)
    _on_step_post(instance)
    return rv


def install() -> None:
    """Idempotent."""
    global _INSTALLED
    if _INSTALLED:
        return
    try:
        m = _import_lr_scheduler()
    except Exception:
        log.debug("pytorch._scheduler_bridge: torch.lr_scheduler unavailable", exc_info=True)
        return

    wrapped_any = False
    for cls_name in _CANDIDATES:
        cls = getattr(m, cls_name, None)
        if cls is None:
            continue
        # In some torch versions _LRScheduler is just an alias of LRScheduler;
        # skip the duplicate to avoid double-wrapping the same callable.
        if any(prev_cls is cls for prev_cls, _ in _INSTALLED_WRAPS):
            continue
        try:
            wrapt.wrap_function_wrapper(m, cls_name + ".step", _wrapped_step)
            _INSTALLED_WRAPS.append((cls, cls_name))
            wrapped_any = True
        except Exception:
            log.debug("pytorch._scheduler_bridge: wrap failed for %s.step", cls_name, exc_info=True)

    if wrapped_any:
        _INSTALLED = True


def uninstall() -> None:
    """Idempotent."""
    global _INSTALLED, _last_lr
    if not _INSTALLED and not _INSTALLED_WRAPS:
        return
    for cls, _ in list(_INSTALLED_WRAPS):
        try:
            if hasattr(cls.step, "__wrapped__"):
                cls.step = cls.step.__wrapped__
        except Exception:
            log.debug("pytorch._scheduler_bridge: uninstall step failed", exc_info=True)
    _INSTALLED_WRAPS.clear()
    _INSTALLED = False
    _last_lr = None
