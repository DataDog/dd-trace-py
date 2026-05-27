"""Layer 2 step-level profiling hooks for the PyTorch contrib integration.

Gated by ``DD_PYTORCH_PROFILING=true``. When enabled, emits a
``pytorch.step`` root span per actual ``optimizer.step()`` call with
``pytorch.data_load`` / ``pytorch.forward`` / ``pytorch.backward`` /
``pytorch.optimizer`` children. Reuses Layer 1's framework registry, AMP
state, and CUDA Event resolver as-is.
"""

import sys
import threading
from typing import Any
from typing import Optional
import weakref

from ddtrace import config
from ddtrace import tracer
from ddtrace.contrib.internal.pytorch._utils import _amp_skip_state
from ddtrace.contrib.internal.pytorch._utils import get_last_optimizer_step_end_ns
from ddtrace.contrib.internal.pytorch._utils import get_rank
from ddtrace.contrib.internal.pytorch._utils import now_ns
from ddtrace.contrib.internal.pytorch._utils import set_last_optimizer_step_end_ns
from ddtrace.contrib.internal.pytorch._utils import set_training_job_id_tag
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env
from ddtrace.internal.utils.formats import asbool


log = get_logger(__name__)

PROFILING_ENABLED: bool = asbool(env.get("DD_PYTORCH_PROFILING", False))
STEP_OPTIMIZER_NAME: Optional[str] = env.get("DD_PYTORCH_STEP_OPTIMIZER") or None


def is_profiling_enabled() -> bool:
    """Layer Two enablement, read at call time so late env changes propagate."""
    return asbool(env.get("DD_PYTORCH_PROFILING", "false"))


# Designation state (which optimizer instance delimits the canonical step
# boundary when multiple optimizers are present, e.g. GAN-style training).
_designation_lock = threading.Lock()
_designated_optimizer_ref: Optional["weakref.ref[Any]"] = None
_designated_warning_emitted: bool = False
_step_counter: int = 0

# Per-thread state.
_FORWARD_TLS = threading.local()  # stack of currently-open pytorch.forward spans
_STEP_TLS = threading.local()  # currently-open pytorch.step root span
_DATA_LOAD_TLS = threading.local()  # whether pytorch.data_load was emitted in the current step

# Cap on the per-thread forward stack — protects against unbounded growth if
# user `forward` raises and PyTorch never invokes the matching `forward_hook`.
_FORWARD_STACK_MAX = 16

# Marker set on a model to skip duplicate hook registration.
_HOOKED_FLAG_ATTR = "_dd_layer_two_hooked"

# Per-model list of removable hook handles, used by `detach_layer_two_hooks`
# on unpatch. WeakKeyDictionary so destroyed models are auto-evicted.
_HOOK_HANDLES: "weakref.WeakKeyDictionary[Any, list]" = weakref.WeakKeyDictionary()


# ---------------------------------------------------------------------------
# Designation
# ---------------------------------------------------------------------------


def _is_designated(instance: Any) -> bool:
    ref = _designated_optimizer_ref
    return ref is not None and ref() is instance


def _maybe_designate(instance: Any) -> None:
    """Designate the first matching optimizer instance to delimit steps.

    With ``DD_PYTORCH_STEP_OPTIMIZER`` set, the first instance whose class
    name matches becomes designated. Without the env var, the first instance
    to call ``step()`` becomes designated. If the designated instance is
    garbage-collected, the next call re-designates.
    """
    global _designated_optimizer_ref, _designated_warning_emitted
    with _designation_lock:
        if _designated_optimizer_ref is not None and _designated_optimizer_ref() is None:
            # Designated instance was GC'd; allow re-designation.
            _designated_optimizer_ref = None
            _designated_warning_emitted = False
        if _designated_optimizer_ref is not None:
            return
        if STEP_OPTIMIZER_NAME is not None and type(instance).__name__ != STEP_OPTIMIZER_NAME:
            return
        _designated_optimizer_ref = weakref.ref(instance)
        if not _designated_warning_emitted:
            log.warning(
                "ddtrace pytorch Layer 2: designated optimizer for step counter is %s (id=%s)",
                type(instance).__name__,
                id(instance),
            )
            _designated_warning_emitted = True


# ---------------------------------------------------------------------------
# Step boundary
# ---------------------------------------------------------------------------


def _step_parent():
    return getattr(_STEP_TLS, "span", None) or tracer.current_span()


def _ensure_step_open() -> None:
    if not is_profiling_enabled():
        return
    if getattr(_STEP_TLS, "span", None) is not None:
        return
    # Capture whatever was active before our `pytorch.step` takes over so we
    # can restore it in `_close_step` — `span.finish()` doesn't deactivate.
    _STEP_TLS.prev_active = tracer.context_provider.active()
    span = tracer.start_span("pytorch.step", service=config.pytorch.service, activate=True)
    span.set_tag("component", "pytorch")
    span.set_tag("debug.level", "2")
    span._set_attribute("rank", get_rank())
    set_training_job_id_tag(span)
    # Back-date the step root to the previous step's end so the `pytorch.data_load`
    # child (which is anchored at `prev_end`) doesn't start before its parent.
    prev_end = get_last_optimizer_step_end_ns()
    if prev_end > 0:
        span.start_ns = prev_end
    _STEP_TLS.span = span


def _close_step(skipped: bool = False) -> None:
    span = getattr(_STEP_TLS, "span", None)
    if span is None:
        return
    if skipped:
        span.set_tag("skipped", True)
    else:
        span._set_attribute("step", _step_counter)
    span.finish()
    # Restore whatever was active before our step took over so subsequent
    # spans on this thread aren't mis-parented under the finished step.
    try:
        tracer.context_provider.activate(getattr(_STEP_TLS, "prev_active", None))
    except Exception:
        log.debug("pytorch: failed to restore pre-step active context", exc_info=True)
    _STEP_TLS.span = None
    _STEP_TLS.prev_active = None
    # Reset the per-step data_load marker so the next iteration's first forward
    # can emit `pytorch.data_load` again.
    _DATA_LOAD_TLS.emitted = False


def _maybe_close_step(instance: Any) -> None:
    if not _is_designated(instance):
        return
    # Hold the designation lock for the counter increment too: it's not the
    # hot path, and it cheaply guards against multi-thread races between
    # designated-step closures (rare but possible in custom training loops).
    global _step_counter
    with _designation_lock:
        _step_counter += 1
        current_step = _step_counter
    # Capture span identity before _close_step nulls _STEP_TLS.span.
    finished_span = getattr(_STEP_TLS, "span", None)
    _close_step(skipped=False)
    # Notify the Layer 3 profiler (no-op when DD_PYTORCH_KERNEL_PROFILING is unset).
    if finished_span is not None:
        try:
            from ddtrace.contrib.internal.pytorch import _profiler
            from ddtrace.contrib.internal.pytorch._distributed import _state

            _profiler.on_designated_step_finished(
                span=finished_span,
                step=current_step,
                rank=int(_state.get("rank", 0) or 0),
            )
        except Exception:
            log.debug("pytorch: Layer 3 hook failed", exc_info=True)


def _mark_optimizer_step_end_now() -> None:
    set_last_optimizer_step_end_ns(now_ns())


def _emit_data_load_span_if_needed() -> None:
    # Emit at most once per pytorch.step (the first forward of an iteration);
    # subsequent forwards in the same step (gradient accumulation, LBFGS
    # closure) would otherwise emit duplicates with the same start_ns.
    if getattr(_DATA_LOAD_TLS, "emitted", False):
        return
    prev_end = get_last_optimizer_step_end_ns()
    if prev_end == 0:
        return
    end_ns = now_ns()
    span = tracer.start_span(
        "pytorch.data_load",
        service=config.pytorch.service,
        child_of=_step_parent(),
    )
    span.start_ns = prev_end
    span.set_tag("component", "pytorch")
    span.set_tag("debug.level", "2")
    span._set_attribute("rank", get_rank())
    set_training_job_id_tag(span)
    span.finish(finish_time=end_ns / 1e9)
    _DATA_LOAD_TLS.emitted = True


# ---------------------------------------------------------------------------
# Forward / backward hooks
# ---------------------------------------------------------------------------


def _forward_pre_hook(module: Any, inputs: Any) -> None:
    if not is_profiling_enabled():
        return
    try:
        _ensure_step_open()
        _emit_data_load_span_if_needed()
        span = tracer.start_span(
            "pytorch.forward",
            service=config.pytorch.service,
            child_of=_step_parent(),
        )
        span.set_tag("component", "pytorch")
        span.set_tag("debug.level", "2")
        span._set_attribute("rank", get_rank())
        set_training_job_id_tag(span)
        stack = getattr(_FORWARD_TLS, "stack", None)
        if stack is None:
            stack = []
            _FORWARD_TLS.stack = stack
        if len(stack) >= _FORWARD_STACK_MAX:
            # Likely a leak from a previous forward that raised before its
            # matching forward_hook fired. Drop the stale spans.
            for stale in stack:
                try:
                    stale.set_tag("_dd.error_reason", "forward_hook_leak")
                    stale.finish()
                except Exception:
                    pass
            stack.clear()
        stack.append(span)
    except Exception:
        log.debug("pytorch forward_pre_hook failed", exc_info=True)


def _forward_hook(module: Any, inputs: Any, output: Any) -> None:
    if not is_profiling_enabled():
        return
    try:
        stack = getattr(_FORWARD_TLS, "stack", None) or []
        if not stack:
            return
        stack.pop().finish()
    except Exception:
        log.debug("pytorch forward_hook failed", exc_info=True)


def _full_backward_hook(module: Any, grad_input: Any, grad_output: Any) -> None:
    if not is_profiling_enabled():
        return
    try:
        _ensure_step_open()
        span = tracer.start_span(
            "pytorch.backward",
            service=config.pytorch.service,
            child_of=_step_parent(),
        )
        span.set_tag("component", "pytorch")
        span.set_tag("debug.level", "2")
        span._set_attribute("rank", get_rank())
        set_training_job_id_tag(span)
        span.finish()
    except Exception:
        log.debug("pytorch full_backward_hook failed", exc_info=True)


# ---------------------------------------------------------------------------
# Optimizer step (replaces Layer 1's pure-timing pass-through)
# ---------------------------------------------------------------------------


def optimizer_step(wrapped, instance, args, kwargs):
    """Called from Layer 1's instance-level optimizer step wrapper.

    When Layer 2 is disabled, falls through transparently. When enabled,
    emits a ``pytorch.optimizer`` span covering the actual step (in both AMP
    and non-AMP modes) and — outside of AMP — closes the active
    ``pytorch.step`` via the designation policy. In AMP, ``pytorch.step`` is
    closed by ``gradscaler_emit_step_outcome``.
    """
    if not is_profiling_enabled():
        return wrapped(*args, **kwargs)

    inside_gradscaler = getattr(_amp_skip_state, "in_amp", False)
    _ensure_step_open()
    _maybe_designate(instance)
    span = tracer.start_span(
        "pytorch.optimizer",
        service=config.pytorch.service,
        child_of=_step_parent(),
    )
    span.set_tag("component", "pytorch")
    span.set_tag("debug.level", "2")
    span.set_tag("optimizer", type(instance).__name__)
    span._set_attribute("rank", get_rank())
    set_training_job_id_tag(span)
    raised = False
    try:
        result = wrapped(*args, **kwargs)
        if inside_gradscaler:
            _amp_skip_state.step_executed = True
        return result
    except BaseException:
        raised = True
        span.set_exc_info(*sys.exc_info())
        raise
    finally:
        span.finish()
        # When the user's optimizer.step raised, do not advance the step
        # counter or close the parent pytorch.step span — let the next
        # successful step complete the cycle.
        if not raised:
            _mark_optimizer_step_end_now()
            if not inside_gradscaler:
                _maybe_close_step(instance)


def gradscaler_emit_step_outcome(optimizer: Any, skipped: bool) -> None:
    """Called from Layer 1's GradScaler wrapper after the inner step decision.

    Only the designated optimizer's outcome drives `pytorch.step` closure;
    a non-designated optimizer's AMP skip is invisible to the step counter.
    """
    if not is_profiling_enabled():
        return
    if optimizer is None:
        return
    # Allow first-AMP-step to designate the optimizer too (covers the case
    # where the user wires GradScaler around the canonical optimizer without
    # ever invoking it outside AMP).
    _maybe_designate(optimizer)
    if not _is_designated(optimizer):
        return
    if skipped:
        _close_step(skipped=True)
        return
    _maybe_close_step(optimizer)


# ---------------------------------------------------------------------------
# Hook attachment
# ---------------------------------------------------------------------------


def attach_layer_two_hooks(model: Any) -> None:
    """Idempotently register Layer 2 hooks on a top-level user model.

    Called from DDP/FSDP/DeepSpeed ``__init__`` wrappers (and a single-GPU
    bare-optimizer fallback). No-op when ``DD_PYTORCH_PROFILING`` is unset.
    Returned hook handles are stored on a per-model WeakKeyDictionary so
    ``detach_layer_two_hooks`` can cleanly remove them on unpatch.
    """
    if not is_profiling_enabled():
        return
    try:
        if getattr(model, _HOOKED_FLAG_ATTR, False):
            return
        handles = [
            model.register_forward_pre_hook(_forward_pre_hook),
            model.register_forward_hook(_forward_hook),
            model.register_full_backward_hook(_full_backward_hook),
        ]
        _HOOK_HANDLES[model] = handles
        setattr(model, _HOOKED_FLAG_ATTR, True)
    except Exception:
        log.debug("pytorch attach_layer_two_hooks failed", exc_info=True)


def detach_layer_two_hooks() -> None:
    """Remove every Layer 2 hook handle registered via ``attach_layer_two_hooks``."""
    for model, handles in list(_HOOK_HANDLES.items()):
        for h in handles:
            try:
                h.remove()
            except Exception:
                log.debug("pytorch hook removal failed", exc_info=True)
        try:
            delattr(model, _HOOKED_FLAG_ATTR)
        except Exception:
            pass
    _HOOK_HANDLES.clear()
