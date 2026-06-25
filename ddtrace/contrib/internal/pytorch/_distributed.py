"""Distributed-training bootstrap: wraps init/destroy_process_group to
open and close the pytorch.rank lifetime span.
"""

import contextvars
import threading
from typing import Any
from typing import Optional

import torch

from ddtrace.contrib.internal.pytorch._utils import get_cached_job_id
from ddtrace.contrib.internal.pytorch._utils import job_id_env_set
from ddtrace.contrib.internal.pytorch._utils import resolve_job_id_from_env
from ddtrace.contrib.internal.pytorch._utils import set_cached_job_id
from ddtrace.contrib.internal.trace_utils import unwrap as _unwrap
from ddtrace.contrib.internal.trace_utils import wrap as _wrap
from ddtrace.internal import core
from ddtrace.internal import forksafe
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)

_no_env_job_id_warned: bool = False
_installed: bool = False
_optimizer_wrapped: bool = False
_grad_scaler_wrapped: bool = False
_clip_grad_norm_wrapped: bool = False
_fsdp_hook_registered: bool = False
_deepspeed_hook_registered: bool = False
# Counter for the optimizer.step wrap. First fire emits an INFO log so we can
# diagnose whether Ray Train / Lightning / DeepSpeed route through the public
# Optimizer.step or a vendored fast path that bypasses our wrap.
_optimizer_step_fire_count: int = 0

# Tracks the active ExecutionContext for the current distributed training session.
# Presence (non-None) doubles as the "bootstrapped" flag.
# AIDEV-NOTE: ContextVar is per-thread — safe because init/destroy_process_group always run on the same thread in DDP.
_rank_ctx: contextvars.ContextVar[Optional[core.ExecutionContext[Any]]] = contextvars.ContextVar(
    "pytorch_rank_ctx", default=None
)

_cached_distributed_backend: Optional[str] = None


def _inner_model(instance: Any) -> Any:
    """Unwrap a framework wrapper (DDP / FSDP / DeepSpeedEngine) to the
    underlying ``nn.Module`` used for parameter / module-tree walks.

    All three wrappers expose the inner module on a ``.module`` attribute
    on supported torch versions. Plain ``nn.Module`` instances degrade to
    themselves. Done centrally so the c_bridge param-count and
    is_transformer detection sees the same view regardless of which
    framework wrapper installed it.
    """
    return getattr(instance, "module", instance)


def _step_profiling_enabled() -> bool:
    return env.get("DD_TRAINING_STEP_PROFILING", "false").lower() in ("true", "1")


# Wire-format env var names set by the Ray contrib on worker processes.
# AIDEV-NOTE: duplicated from ddtrace.contrib.internal.ray intentionally —
# contrib-to-contrib imports break isolation (ray contrib may not be installed).
# If these names ever change, update both sides.
_RAY_SUBMISSION_ID_ENV = "_RAY_SUBMISSION_ID"
_RAY_JOB_NAME_ENV = "_RAY_JOB_NAME"
_RAY_RUN_METADATA_ENV = "_DD_RAY_RUN_METADATA"


def _reset_optimizer_step_count() -> None:
    """Test / forksafe helper: reset the one-shot fire log so a fresh
    process emits its info line again."""
    global _optimizer_step_fire_count
    _optimizer_step_fire_count = 0


def _reset_child_state() -> None:
    global \
        _no_env_job_id_warned, \
        _cached_distributed_backend, \
        _fsdp_hook_registered, \
        _deepspeed_hook_registered, \
        _optimizer_wrapped
    ctx = _rank_ctx.get()
    if ctx is not None:
        _rank_ctx.set(None)
        # AIDEV-NOTE: Deferred imports + manual reset — import system may be unsafe post-fork.
        try:
            from ddtrace.internal.core import _CURRENT_CONTEXT  # noqa: PLC0415
            from ddtrace.internal.core import ROOT_CONTEXT_ID  # noqa: PLC0415
            from ddtrace.internal.core import ExecutionContext  # noqa: PLC0415

            _CURRENT_CONTEXT.set(ExecutionContext(ROOT_CONTEXT_ID))
        except Exception:  # nosec B110
            pass
    _no_env_job_id_warned = False
    _cached_distributed_backend = None
    _fsdp_hook_registered = False
    _deepspeed_hook_registered = False
    _optimizer_wrapped = False


forksafe.register(_reset_child_state)


def _distributed_available() -> bool:
    try:
        return bool(torch.distributed.is_available())
    except Exception:
        return False


def _get_cached_backend() -> Optional[str]:
    """One-shot lookup of ``torch.distributed.get_backend()``. Caches the
    result on first successful call. The backend (nccl/gloo/mpi) does not
    change during the lifetime of a process group.
    """
    global _cached_distributed_backend
    if _cached_distributed_backend is not None:
        return _cached_distributed_backend
    try:
        if _distributed_available() and torch.distributed.is_initialized():
            _cached_distributed_backend = str(torch.distributed.get_backend())
    except Exception:
        return None
    return _cached_distributed_backend


def _populate_ray_run_metadata() -> None:
    """Read Ray-set env vars into the run-metadata cache so _tag_ray_run_context can find them."""
    sub = env.get(_RAY_SUBMISSION_ID_ENV)
    rn = env.get(_RAY_JOB_NAME_ENV)
    md_json = env.get(_RAY_RUN_METADATA_ENV)
    metadata: dict[str, Any] = {}
    if md_json:
        try:
            import json  # noqa: PLC0415

            metadata = json.loads(md_json) or {}
        except Exception:  # nosec B110
            pass
    if sub or rn or metadata:
        from ddtrace.contrib.internal.pytorch._utils import set_cached_run_metadata  # noqa: PLC0415

        set_cached_run_metadata(submission_id=sub, run_name=rn, metadata=metadata or None)


def _detect_launcher() -> Optional[str]:
    """Return a best-guess launcher name from env, or None."""
    if env.get("TORCHELASTIC_RUN_ID"):
        return "torchrun"
    if env.get("RAY_JOB_ID"):
        return "ray"
    if env.get("SLURM_JOB_ID"):
        return "slurm"
    if env.get("KUBEFLOW_TRAINING_JOB_ID"):
        return "kubeflow"
    return None


def _bootstrap_distributed() -> None:
    """Capture rank/world_size and open the pytorch.rank span.

    Cross-rank correlation requires an env-supplied id (RAY_JOB_ID,
    TORCHELASTIC_RUN_ID, KUBEFLOW_TRAINING_JOB_ID, SLURM_JOB_ID). When none
    is resolved, training_job.id is left unset so missing correlation is visible.
    """
    global _no_env_job_id_warned

    cached = get_cached_job_id()
    env_id_present = job_id_env_set()
    job_id = cached or resolve_job_id_from_env()

    rank: int = 0
    world_size: int = 1
    try:
        if _distributed_available() and torch.distributed.is_initialized():
            rank = torch.distributed.get_rank()
            world_size = torch.distributed.get_world_size()
    except Exception:
        log.exception("pytorch: failed to capture rank/world_size; defaulting to single-rank")

    publishable_job_id: Optional[str] = job_id
    if not cached and not env_id_present:
        publishable_job_id = None
        if not _no_env_job_id_warned:
            log.warning(
                "pytorch: no shared training job id resolved from env "
                "(DD_PYTORCH_JOB_ID, RAY_JOB_ID, TORCHELASTIC_RUN_ID, "
                "KUBEFLOW_TRAINING_JOB_ID, SLURM_JOB_ID). Cross-rank trace "
                "correlation will be DISABLED for this run — spans will not "
                "carry the training_job.id tag."
            )
            _no_env_job_id_warned = True

    if publishable_job_id is not None:
        set_cached_job_id(publishable_job_id, is_default=True)

    _populate_ray_run_metadata()

    from ddtrace.contrib.internal.pytorch import _device  # noqa: PLC0415
    from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

    try:
        _device.discover(local_rank=rank)
    except Exception:
        log.exception("pytorch: device discovery failed")

    try:
        _rank_root.open_rank_span(
            rank=rank,
            world_size=world_size,
            framework="none",
            training_job_id=publishable_job_id,
        )
    except Exception:
        log.exception("pytorch: rank-root span open failed")

    if _step_profiling_enabled():
        try:
            from ddtrace.contrib.internal.pytorch import _c_tracer  # noqa: PLC0415

            _c_tracer.step_begin()
        except Exception:
            log.debug("pytorch: step_begin after bootstrap failed", exc_info=True)


def _wrapped_init_process_group(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    already = _rank_ctx.get() is not None

    result = wrapped(*args, **kwargs)  # let exceptions propagate; do NOT open context yet

    if not already:
        ctx = core.context_with_data("pytorch.rank", _dispatch_end_event=False)  # type: ignore[no-untyped-call]
        # AIDEV-NOTE: __enter__() updates _CURRENT_CONTEXT so child spans are parented here; _dispatch_end_event=False
        # defers the ended event — dispatch_ended_event() + __exit__() are called in _wrapped_destroy_process_group.
        ctx.__enter__()
        _rank_ctx.set(ctx)
        try:
            _bootstrap_distributed()
        except Exception:
            log.exception("pytorch: bootstrap failed inside init_process_group wrapper")
    return result


def _is_world_group(group: Any) -> bool:
    """Return True if group is the default WORLD process group.

    Handles both the no-arg (None) and the explicit group=torch.distributed.group.WORLD forms.
    """
    if group is None:
        return True
    try:
        return group is torch.distributed.group.WORLD
    except AttributeError:
        return False


def _wrapped_destroy_process_group(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    group = kwargs.get("group", args[0] if args else None)
    try:
        result = wrapped(*args, **kwargs)
        return result
    finally:
        # Close the rank span only when the default (WORLD) process group is
        # destroyed. Subgroup destroys must not end the span.
        if _is_world_group(group):
            if _step_profiling_enabled():
                try:
                    from ddtrace.contrib.internal.pytorch import _c_tracer  # noqa: PLC0415

                    _c_tracer.step_end()
                except Exception:
                    log.debug("pytorch: step_end before rank-root close failed", exc_info=True)
            try:
                from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

                _rank_root.close()
            except Exception:
                log.debug("pytorch: rank-root close raised", exc_info=True)
            ctx = _rank_ctx.get()
            if ctx is not None:
                ctx.dispatch_ended_event()
                ctx.__exit__(None, None, None)
                _rank_ctx.set(None)
            global _cached_distributed_backend
            _cached_distributed_backend = None
            try:
                from ddtrace.contrib.internal.pytorch._utils import set_cached_job_id  # noqa: PLC0415

                set_cached_job_id(None, is_default=True)
            except Exception:  # nosec B110
                pass


def _wrapped_ddp_init(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    result = wrapped(*args, **kwargs)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

        _rank_root.set_framework("ddp")
        _rank_root.set_model(_inner_model(instance))
    except Exception:
        log.debug("pytorch: failed to update rank-root framework tag", exc_info=True)
    return result


def _install_ddp() -> None:
    try:
        import torch.nn.parallel.distributed  # noqa: F401
    except Exception:
        return
    if not hasattr(torch.nn.parallel.distributed, "DistributedDataParallel"):
        return
    _wrap(
        "torch.nn.parallel.distributed",
        "DistributedDataParallel.__init__",
        _wrapped_ddp_init,
    )


def _uninstall_ddp() -> None:
    try:
        import torch.nn.parallel.distributed  # noqa: F401
    except Exception:
        return
    if not hasattr(torch.nn.parallel.distributed, "DistributedDataParallel"):
        return
    try:
        _unwrap(torch.nn.parallel.distributed.DistributedDataParallel, "__init__")
    except Exception:
        log.debug("pytorch: failed to unwrap DDP.__init__", exc_info=True)


def _wrapped_fsdp_init(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    result = wrapped(*args, **kwargs)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

        _rank_root.set_framework("fsdp")
        _rank_root.set_model(_inner_model(instance))
    except Exception:
        log.debug("pytorch: failed to update rank-root framework tag", exc_info=True)
    return result


def _install_fsdp() -> None:
    # AIDEV-NOTE: defer the import of torch.distributed.fsdp until the user
    # actually imports it. Eagerly importing it pulls _dynamo + sympy (~1.3s
    # startup cost) for every DDP workload that never uses FSDP.
    global _fsdp_hook_registered
    if _fsdp_hook_registered:
        return
    from wrapt import register_post_import_hook

    def _do_install(_module: object) -> None:
        if not _installed:
            return
        try:
            import torch.distributed.fsdp as _fsdp  # noqa: PLC0415

            if hasattr(_fsdp.FullyShardedDataParallel.__init__, "__wrapped__"):
                return
            _wrap(
                "torch.distributed.fsdp",
                "FullyShardedDataParallel.__init__",
                _wrapped_fsdp_init,
            )
        except Exception:
            log.exception("pytorch: failed to install FSDP wrapper")

    register_post_import_hook(_do_install, "torch.distributed.fsdp")
    _fsdp_hook_registered = True


def _uninstall_fsdp() -> None:
    try:
        from torch.distributed.fsdp import FullyShardedDataParallel
    except Exception:
        return
    try:
        _unwrap(FullyShardedDataParallel, "__init__")
    except Exception:
        log.debug("pytorch: failed to unwrap FSDP.__init__", exc_info=True)


def _wrapped_deepspeed_init(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    result = wrapped(*args, **kwargs)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

        _rank_root.set_framework("deepspeed")
        # deepspeed.initialize() returns a tuple whose first element is
        # the DeepSpeedEngine; the engine's .module is the underlying
        # nn.Module that _c_bridge needs for parameter introspection.
        engine = result[0] if isinstance(result, tuple) and result else result
        _rank_root.set_model(_inner_model(engine))
    except Exception:
        log.debug("pytorch: failed to update rank-root framework tag", exc_info=True)
    return result


def _install_deepspeed() -> None:
    global _deepspeed_hook_registered
    if _deepspeed_hook_registered:
        return
    from wrapt import register_post_import_hook

    def _do_install(deepspeed: object) -> None:
        if not _installed:
            return
        if not hasattr(deepspeed, "initialize"):
            return
        if hasattr(deepspeed.initialize, "__wrapped__"):
            return
        try:
            _wrap("deepspeed", "initialize", _wrapped_deepspeed_init)
        except Exception:
            log.exception("pytorch: failed to install deepspeed wrapper")

    register_post_import_hook(_do_install, "deepspeed")
    _deepspeed_hook_registered = True


def _uninstall_deepspeed() -> None:
    try:
        import deepspeed  # noqa: F401
    except Exception:
        return
    try:
        _unwrap(deepspeed, "initialize")
    except Exception:
        log.debug("pytorch: failed to unwrap deepspeed.initialize", exc_info=True)


def _install_optimizer_step() -> None:
    global _optimizer_wrapped
    if _optimizer_wrapped or not _step_profiling_enabled():
        return
    if not hasattr(torch.optim, "Optimizer"):
        return
    try:
        _wrap("torch.optim", "Optimizer.step", _wrapped_optimizer_step)
        _optimizer_wrapped = True
    except Exception:
        log.debug("pytorch: failed to wrap Optimizer.step", exc_info=True)


def _uninstall_optimizer_step() -> None:
    global _optimizer_wrapped
    if not _optimizer_wrapped:
        return
    if not hasattr(torch.optim, "Optimizer"):
        return
    try:
        _unwrap(torch.optim.Optimizer, "step")
    except Exception:
        log.debug("pytorch: failed to unwrap Optimizer.step", exc_info=True)
    _optimizer_wrapped = False


def _wrapped_optimizer_step(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    from ddtrace.contrib.internal.pytorch import _c_tracer  # noqa: PLC0415

    global _optimizer_step_fire_count
    _optimizer_step_fire_count += 1
    # One-shot info log so we can confirm the wrap is actually firing.
    # Frameworks that use a vendored fast path (DeepSpeed engine.step,
    # FSDP's internal optimizer step, ...) bypass torch.optim.Optimizer.step
    # and our wrap never fires — without this log the failure mode is silent.
    if _optimizer_step_fire_count == 1:
        log.info(
            "pytorch: Optimizer.step wrap fired for the first time on %s — step-boundary signalling active",
            type(instance).__name__,
        )

    _c_tracer.step_end()  # close step N: optimizer phase ends
    result = wrapped(*args, **kwargs)
    _c_tracer.step_begin()  # open step N+1: forward starts
    return result


def _wrapped_grad_scaler_step(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    """Wrap ``torch.cuda.amp.GradScaler.step`` to detect skipped steps.

    GradScaler.step returns the wrapped optimizer.step()'s return value when
    it actually steps, or ``None`` when it determined gradients were
    inf/NaN and skipped the step. Bumping the AMP-skip counter on every
    ``None`` return matches what the C tracer's heuristic D2H-pattern
    detection counts — but doing it from Python is deterministic.
    """
    from ddtrace.contrib.internal.pytorch import _c_tracer  # noqa: PLC0415

    result = wrapped(*args, **kwargs)
    if result is None:
        try:
            _c_tracer.record_amp_skipped()
        except Exception:
            log.debug("pytorch: record_amp_skipped failed", exc_info=True)
    return result


def _install_grad_scaler() -> None:
    global _grad_scaler_wrapped
    if _grad_scaler_wrapped:
        return
    # torch.cuda.amp.GradScaler is the legacy path; torch.amp.GradScaler is
    # the modern alias (PyTorch >= 2.4). They are the same class object on
    # supported versions, so wrapping one wraps both — but wrapping via
    # different module attribute names twice would double-wrap. Prefer the
    # legacy path because every supported torch version exposes it.
    try:
        import torch.cuda.amp  # noqa: F401, PLC0415
    except Exception:
        return
    if not hasattr(torch.cuda.amp, "GradScaler"):
        return
    try:
        _wrap("torch.cuda.amp", "GradScaler.step", _wrapped_grad_scaler_step)
        _grad_scaler_wrapped = True
    except Exception:
        log.debug("pytorch: failed to wrap GradScaler.step", exc_info=True)


def _uninstall_grad_scaler() -> None:
    global _grad_scaler_wrapped
    if not _grad_scaler_wrapped:
        return
    try:
        import torch.cuda.amp  # noqa: PLC0415

        _unwrap(torch.cuda.amp.GradScaler, "step")
    except Exception:
        log.debug("pytorch: failed to unwrap GradScaler.step", exc_info=True)
    _grad_scaler_wrapped = False


def _wrapped_clip_grad_norm(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    """Wrap ``torch.nn.utils.clip_grad_norm_`` to feed the grad-clip wall-time sketch.

    Times the call and reports nanoseconds to the C tracer. The wrap is
    cheap (one ``perf_counter_ns`` pair) compared to the actual grad
    clip (a D2H sync of the norm), so the overhead is in the noise.
    """
    import time  # noqa: PLC0415

    from ddtrace.contrib.internal.pytorch import _c_tracer  # noqa: PLC0415

    t0 = time.perf_counter_ns()
    try:
        return wrapped(*args, **kwargs)
    finally:
        try:
            _c_tracer.record_grad_clip_ns(time.perf_counter_ns() - t0)
        except Exception:
            log.debug("pytorch: record_grad_clip_ns failed", exc_info=True)


def _install_clip_grad_norm() -> None:
    global _clip_grad_norm_wrapped
    if _clip_grad_norm_wrapped:
        return
    try:
        import torch.nn.utils  # noqa: F401, PLC0415
    except Exception:
        return
    if not hasattr(torch.nn.utils, "clip_grad_norm_"):
        return
    try:
        _wrap("torch.nn.utils", "clip_grad_norm_", _wrapped_clip_grad_norm)
        _clip_grad_norm_wrapped = True
    except Exception:
        log.debug("pytorch: failed to wrap clip_grad_norm_", exc_info=True)


def _uninstall_clip_grad_norm() -> None:
    global _clip_grad_norm_wrapped
    if not _clip_grad_norm_wrapped:
        return
    try:
        import torch.nn.utils  # noqa: PLC0415

        _unwrap(torch.nn.utils, "clip_grad_norm_")
    except Exception:
        log.debug("pytorch: failed to unwrap clip_grad_norm_", exc_info=True)
    _clip_grad_norm_wrapped = False


def install() -> None:
    global _installed
    if _installed:
        return
    _installed = True
    if _distributed_available() and hasattr(torch.distributed, "init_process_group"):
        _wrap("torch.distributed", "init_process_group", _wrapped_init_process_group)
    if _distributed_available() and hasattr(torch.distributed, "destroy_process_group"):
        _wrap("torch.distributed", "destroy_process_group", _wrapped_destroy_process_group)
    _install_ddp()
    _install_fsdp()
    _install_deepspeed()
    _install_optimizer_step()
    _install_grad_scaler()
    _install_clip_grad_norm()
    # Late-patch bootstrap: if init_process_group was called before patch(),
    # our wrapper will never fire. Run bootstrap now.
    if _distributed_available():
        try:
            if torch.distributed.is_initialized() and _rank_ctx.get() is None:
                ctx = core.context_with_data("pytorch.rank", _dispatch_end_event=False)  # type: ignore[no-untyped-call]
                ctx.__enter__()
                _rank_ctx.set(ctx)
                _bootstrap_distributed()
        except Exception:
            log.exception("pytorch: late-patch bootstrap failed")


def uninstall() -> None:
    global _installed, _fsdp_hook_registered, _deepspeed_hook_registered
    if _installed:
        _installed = False
        if _distributed_available():
            for fn in ("destroy_process_group", "init_process_group"):
                if hasattr(torch.distributed, fn):
                    try:
                        _unwrap(torch.distributed, fn)
                    except Exception:
                        log.debug("pytorch: failed to unwrap torch.distributed.%s", fn, exc_info=True)
        _uninstall_ddp()
        _uninstall_fsdp()
        _fsdp_hook_registered = False
        _uninstall_deepspeed()
        _deepspeed_hook_registered = False
        _uninstall_optimizer_step()
        _uninstall_grad_scaler()
        _uninstall_clip_grad_norm()
    try:
        from ddtrace.contrib.internal.pytorch import _device as _device_mod  # noqa: PLC0415

        _device_mod._cache = None
    except Exception:  # nosec B110
        pass
    try:
        from ddtrace.contrib.internal.pytorch._utils import clear_cached_run_metadata  # noqa: PLC0415

        clear_cached_run_metadata()
    except Exception:  # nosec B110
        pass
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

        _rank_root.close()
    except Exception:
        log.debug("pytorch: rank-root close raised in uninstall", exc_info=True)
    ctx = _rank_ctx.get()
    if ctx is not None:
        ctx.dispatch_ended_event()
        ctx.__exit__(None, None, None)
        _rank_ctx.set(None)
    try:
        from ddtrace.contrib.internal.pytorch import _utils as _utils_mod  # noqa: PLC0415

        _utils_mod._default_job_id = None
        _utils_mod._tls_job_id = threading.local()
    except Exception:
        log.debug("pytorch: failed to reset cached job id on uninstall", exc_info=True)
    global _no_env_job_id_warned, _cached_distributed_backend
    _no_env_job_id_warned = False
    _cached_distributed_backend = None
