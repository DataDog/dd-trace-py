"""Distributed-training bootstrap: wraps init/destroy_process_group to
open and close the pytorch.rank lifetime span.
"""

import os
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
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)

_no_env_job_id_warned: bool = False
_installed: bool = False
_fsdp_hook_registered: bool = False
_deepspeed_hook_registered: bool = False

_state: dict[str, Any] = {
    "bootstrapped": False,
    "job_id": None,
    "rank": 0,
    "world_size": 1,
}
_bootstrap_lock = threading.Lock()
_install_lock = threading.Lock()

_cached_distributed_backend: Optional[str] = None

# Wire-format env var names set by the Ray contrib on worker processes.
# AIDEV-NOTE: duplicated from ddtrace.contrib.internal.ray intentionally —
# contrib-to-contrib imports break isolation (ray contrib may not be installed).
# If these names ever change, update both sides.
_RAY_SUBMISSION_ID_ENV = "_RAY_SUBMISSION_ID"
_RAY_JOB_NAME_ENV = "_RAY_JOB_NAME"
_RAY_RUN_METADATA_ENV = "_DD_RAY_RUN_METADATA"


def _reset_child_state() -> None:
    # Mutate _state in place so by-reference imports see the reset.
    global \
        _bootstrap_lock, \
        _no_env_job_id_warned, \
        _cached_distributed_backend, \
        _fsdp_hook_registered, \
        _deepspeed_hook_registered, \
        _installed
    _state.update({"bootstrapped": False, "job_id": None, "rank": 0, "world_size": 1})
    _bootstrap_lock = threading.Lock()
    _no_env_job_id_warned = False
    _cached_distributed_backend = None
    _fsdp_hook_registered = False
    _deepspeed_hook_registered = False
    _installed = False


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_reset_child_state)


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
    if cached:
        _state["job_id"] = cached
    else:
        _state["job_id"] = resolve_job_id_from_env()

    try:
        if _distributed_available() and torch.distributed.is_initialized():
            _state["rank"] = torch.distributed.get_rank()
            _state["world_size"] = torch.distributed.get_world_size()
    except Exception:
        log.exception("pytorch: failed to capture rank/world_size; defaulting to single-rank")

    publishable_job_id: Optional[str] = _state["job_id"]
    if not cached and not env_id_present:
        publishable_job_id = None
        if not _no_env_job_id_warned:
            log.warning(
                "pytorch: no shared training job id resolved from env "
                "(RAY_JOB_ID, TORCHELASTIC_RUN_ID, KUBEFLOW_TRAINING_JOB_ID, "
                "SLURM_JOB_ID). Cross-rank trace correlation will be DISABLED "
                "for this run — spans will not carry the training_job.id tag."
            )
            _no_env_job_id_warned = True

    if publishable_job_id is not None:
        set_cached_job_id(publishable_job_id, is_default=True)

    _populate_ray_run_metadata()

    from ddtrace.contrib.internal.pytorch import _device  # noqa: PLC0415
    from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

    try:
        _device.discover(local_rank=int(_state["rank"] or 0))
    except Exception:
        log.exception("pytorch: device discovery failed")

    try:
        _rank_root.open_rank_span(
            rank=int(_state["rank"] or 0),
            world_size=int(_state["world_size"] or 1),
            framework="none",
            training_job_id=publishable_job_id,
        )
    except Exception:
        log.exception("pytorch: rank-root span open failed")


def _wrapped_init_process_group(wrapped: Any, instance: Any, args: Any, kwargs: Any) -> Any:
    with _bootstrap_lock:
        already = _state["bootstrapped"]

    result = wrapped(*args, **kwargs)  # let exceptions propagate; do NOT mark bootstrapped yet

    if not already:
        with _bootstrap_lock:
            if not _state["bootstrapped"]:
                _state["bootstrapped"] = True
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
            try:
                from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

                _rank_root.close()
            except Exception:
                log.debug("pytorch: rank-root close raised", exc_info=True)
            with _bootstrap_lock:
                _state["bootstrapped"] = False
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


def install() -> None:
    global _installed
    with _install_lock:
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
    # Late-patch bootstrap: if init_process_group was called before patch(),
    # our wrapper will never fire. Run bootstrap now.
    if _distributed_available():
        try:
            if torch.distributed.is_initialized():
                with _bootstrap_lock:
                    already = _state["bootstrapped"]
                    if not already:
                        _state["bootstrapped"] = True
                if not already:
                    _bootstrap_distributed()
        except Exception:
            log.exception("pytorch: late-patch bootstrap failed")


def uninstall() -> None:
    global _installed
    with _install_lock:
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
            global _fsdp_hook_registered
            _fsdp_hook_registered = False
            _uninstall_deepspeed()
            global _deepspeed_hook_registered
            _deepspeed_hook_registered = False
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
    _state.update({"bootstrapped": False, "job_id": None, "rank": 0, "world_size": 1})
    try:
        from ddtrace.contrib.internal.pytorch import _utils as _utils_mod  # noqa: PLC0415

        _utils_mod._default_job_id = None
        _utils_mod._tls_job_id = threading.local()
    except Exception:
        log.debug("pytorch: failed to reset cached job id on uninstall", exc_info=True)
    global _no_env_job_id_warned, _cached_distributed_backend
    _no_env_job_id_warned = False
    _cached_distributed_backend = None
