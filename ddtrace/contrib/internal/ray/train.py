"""Ray Train (``ray.train.torch.TorchTrainer``) instrumentation.

Two wrappers compose the entity-level training-job trace:

* ``_wrapped_torch_trainer_init`` replaces the user's
  ``train_loop_per_worker`` callable with a picklable wrapper instance
  that survives cloudpickle to Ray Train workers.
* ``_wrapped_torch_trainer_fit`` opens the driver-side ``ray.train.fit``
  span, binds W3C parent-context headers into the wrapper, registers the
  span with the long-running-span manager, and tags ``training_job.status``
  on completion or exception.

The wrapper's ``__call__`` runs worker-side and is delegated to
``_run_train_func_in_worker`` (a module-level function so the wrapper
class stays small enough to pickle cleanly).
"""

from __future__ import annotations

import functools
import threading
from typing import Any
from typing import Callable
from typing import Optional

from ddtrace import config as _ddconfig
from ddtrace.contrib.internal.ray.core.utils import _set_runtime_context_attributes
from ddtrace.contrib.internal.ray.span_manager import start_long_running_span
from ddtrace.contrib.internal.ray.span_manager import stop_long_running_span
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env


log = get_logger(__name__)


class _DDTrainFuncWrapper:
    """Picklable wrapper around the user's ``train_loop_per_worker``.

    Instantiated empty in ``TorchTrainer.__init__``. The driver-side
    ``fit`` wrapper calls :meth:`bind_parent_context` immediately after
    opening ``ray.train.fit`` so the W3C headers carrying the parent
    context are present *before* Ray Train cloudpickles this instance to
    workers.
    """

    def __init__(self, fn: Callable[..., Any], *, training_job_id: str) -> None:
        self._fn = fn
        self._training_job_id = training_job_id
        # Optional human-readable run context tagged on root spans.
        # Populated by the driver-side wrapper from ``RunConfig.name`` and
        # ``RuntimeContext.get_submission_id``; ``metadata`` from the Ray
        # JobInfoClient when reachable. Passed across the cloudpickle
        # boundary so workers can stamp the same values on their spans.
        self._run_name: Optional[str] = None
        self._submission_id: Optional[str] = None
        self._run_metadata: dict = {}
        # Populated by ``bind_parent_context`` at fit time. Stored as a
        # dict of W3C trace headers (``traceparent`` / ``tracestate``);
        # the W3C header format is the only ddtrace propagation surface
        # with a stable public schema, which matters because driver and
        # worker may run different ddtrace versions during a rolling
        # upgrade.
        self._parent_headers: dict = {}
        # Preserves __name__/__qualname__/__doc__ so Ray Train logging
        # shows the user's function name, not ``_DDTrainFuncWrapper``.
        functools.update_wrapper(self, fn)

    def bind_parent_context(self, span_context: Any) -> None:
        """Inject W3C parent-context headers into the wrapper before
        Ray Train pickles it to workers.

        Called by the driver-side ``fit`` wrapper after the
        ``ray.train.fit`` span is opened. Safe to call multiple times
        (later calls overwrite — the last call before pickling wins).
        """
        from ddtrace.propagation.http import HTTPPropagator

        headers: dict = {}
        try:
            HTTPPropagator.inject(span_context, headers)  # in-place mutation
        except Exception:
            log.debug("ray.train: failed to inject parent context headers", exc_info=True)
            return
        self._parent_headers = headers

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        # Lazy import to avoid an import cycle: this module imports from
        # ``ddtrace.contrib.internal.ray`` at module load, and
        # ``_run_train_func_in_worker`` pulls in PyTorch context helpers.
        from ddtrace.contrib.internal.ray.train import _run_train_func_in_worker

        return _run_train_func_in_worker(
            self._fn,
            self._training_job_id,
            self._parent_headers,
            args,
            kwargs,
            run_name=getattr(self, "_run_name", None),
            submission_id=getattr(self, "_submission_id", None),
            run_metadata=getattr(self, "_run_metadata", None) or {},
        )


def _resolve_rank() -> Optional[int]:
    """Best-effort rank discovery. Order:
    ``ray.train.get_context`` → legacy session API → ``RANK`` env var.
    Returns ``None`` only when every source is unavailable.
    """
    try:
        import ray.train  # noqa: PLC0415

        ctx = ray.train.get_context()
        return int(ctx.get_world_rank())
    except Exception:
        pass
    try:
        from ray.train._internal.session import get_session  # noqa: PLC0415

        session = get_session()
        if session is not None:
            return int(session.world_rank)
    except Exception:
        pass

    env_rank = env.get("RANK")
    if env_rank is not None:
        try:
            return int(env_rank)
        except ValueError:
            return None
    return None


def _resolve_world_size() -> Optional[int]:
    try:
        import ray.train  # noqa: PLC0415

        return int(ray.train.get_context().get_world_size())
    except Exception:
        pass
    # No legacy session fallback: ray.train._internal.session does not
    # expose `world_size` on older Ray versions; env is the next best source.

    env_ws = env.get("WORLD_SIZE")
    if env_ws is not None:
        try:
            return int(env_ws)
        except ValueError:
            return None
    return None


def _resolve_local_rank_info() -> dict:
    """Pull local_rank / node_rank / local_world_size from
    ``ray.train.get_context()``. Returns a dict containing only the
    fields we read successfully.
    """
    out: dict = {}
    try:
        import ray.train  # noqa: PLC0415

        ctx = ray.train.get_context()
        try:
            v = ctx.get_local_rank()
            if v is not None:
                out["local_rank"] = int(v)
        except Exception:
            pass
        try:
            v = ctx.get_node_rank()
            if v is not None:
                out["node_rank"] = int(v)
        except Exception:
            pass
        try:
            v = ctx.get_local_world_size()
            if v is not None:
                out["local_world_size"] = int(v)
        except Exception:
            pass
    except Exception:
        pass
    return out


def _ray_submission_id_from_env() -> Optional[str]:
    """Return the Ray submission id from ``_RAY_SUBMISSION_ID``.

    Ray sets this env var on every worker (driver actor included) when the
    job was launched via ``ray job submit``. It matches the ``raysubmit_…``
    value shown by ``ray job list`` / the dashboard. Older Ray versions
    do not expose this through ``RuntimeContext`` directly, so the env
    var is the portable surface.
    """

    raw = env.get("_RAY_SUBMISSION_ID")
    if raw:
        v = raw.strip()
        if v:
            return v
    return None


def _resolve_training_job_id_from_ray() -> Optional[str]:
    """Read the per-trainer job id straight from Ray so operators don't
    have to plumb the id through environment variables on every submit.

    Priority: ``_RAY_SUBMISSION_ID`` env (submission-unique, matches what
    ``ray job list`` shows) → Ray ``RuntimeContext.get_job_id()``
    (driver-process id) → ``None`` (no Ray context).
    """
    sub = _ray_submission_id_from_env()
    if sub:
        return sub
    try:
        import ray  # noqa: PLC0415

        ctx = ray.get_runtime_context()
        getter = getattr(ctx, "get_job_id", None)
        if callable(getter):
            jid = getter()
            if jid:
                return str(jid)
    except Exception:
        log.debug("ray.train: could not resolve training_job_id from Ray runtime context", exc_info=True)
    return None


def _tag_ray_train_config(span, instance) -> None:
    """Tag ``span`` with already-loaded ScalingConfig / RunConfig /
    CheckpointConfig / FailureConfig fields.

    All reads are defensive ``getattr(..., None)`` so missing fields on
    older Ray versions don't fail the wrap. Resource dicts are flattened
    as ``<prefix>.<key>`` numeric facets.
    """
    try:
        sc = getattr(instance, "_scaling_config", None) or getattr(instance, "scaling_config", None)
        if sc is not None:
            nw = getattr(sc, "num_workers", None)
            if nw is not None:
                try:
                    span._set_attribute("ray.train.num_workers", int(nw))
                except Exception:
                    pass
            use_gpu = getattr(sc, "use_gpu", None)
            if use_gpu is not None:
                span.set_tag("ray.train.use_gpu", "true" if use_gpu else "false")
            ps = getattr(sc, "placement_strategy", None)
            if ps:
                span.set_tag("ray.train.placement_strategy", str(ps))
            at = getattr(sc, "accelerator_type", None)
            if at:
                span.set_tag("ray.train.accelerator_type", str(at))
            rpw = getattr(sc, "resources_per_worker", None) or {}
            try:
                for k, v in rpw.items():
                    try:
                        span._set_attribute(f"ray.train.resources_per_worker.{k}", float(v))
                    except Exception:
                        pass
            except Exception:
                pass
            tr = getattr(sc, "trainer_resources", None) or {}
            try:
                for k, v in tr.items():
                    try:
                        span._set_attribute(f"ray.train.trainer_resources.{k}", float(v))
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        log.debug("ray.train: scaling_config tagging failed", exc_info=True)
    try:
        rc = getattr(instance, "_run_config", None) or getattr(instance, "run_config", None)
        if rc is not None:
            sp = getattr(rc, "storage_path", None)
            if sp:
                span.set_tag("ray.train.storage_path", str(sp))
            fc = getattr(rc, "failure_config", None)
            if fc is not None:
                mf = getattr(fc, "max_failures", None)
                if mf is not None:
                    try:
                        span._set_attribute("ray.train.max_failures", int(mf))
                    except Exception:
                        pass
            ck = getattr(rc, "checkpoint_config", None)
            if ck is not None:
                ntk = getattr(ck, "num_to_keep", None)
                if ntk is not None:
                    try:
                        span._set_attribute("ray.train.checkpoint.num_to_keep", int(ntk))
                    except Exception:
                        pass
                freq = getattr(ck, "checkpoint_frequency", None)
                if freq is not None:
                    try:
                        span._set_attribute("ray.train.checkpoint.frequency", int(freq))
                    except Exception:
                        pass
                attr = getattr(ck, "checkpoint_score_attribute", None)
                if attr:
                    span.set_tag("ray.train.checkpoint.score_attribute", str(attr))
                order = getattr(ck, "checkpoint_score_order", None)
                if order:
                    span.set_tag("ray.train.checkpoint.score_order", str(order))
    except Exception:
        log.debug("ray.train: run_config tagging failed", exc_info=True)


def _resolve_run_name(instance: Any) -> Optional[str]:
    """Pull Ray Train ``RunConfig.name`` off the user's Trainer instance.
    Tries the private attribute first (Ray's own convention), then the
    public one so a future API rename doesn't break the read.
    """
    cfg = getattr(instance, "_run_config", None) or getattr(instance, "run_config", None)
    if cfg is None:
        return None
    name = getattr(cfg, "name", None)
    if isinstance(name, str) and name.strip():
        return name.strip()
    return None


_RUN_METADATA_TIMEOUT_S = 2.0


def _resolve_run_metadata(submission_id: Optional[str]) -> dict:
    """Best-effort fetch of submission metadata via Ray's JobSubmissionClient.

    Bounded by `_RUN_METADATA_TIMEOUT_S`. Returns empty dict on timeout
    or failure so instrumentation never blocks the user's fit() call.
    """
    if not submission_id:
        return {}

    result: dict = {}
    done = threading.Event()

    def fetch():
        try:
            from ray.job_submission import JobSubmissionClient  # noqa: PLC0415

            client = JobSubmissionClient()
            info = client.get_job_info(submission_id)
            result.update(dict(getattr(info, "metadata", None) or {}))
        except Exception:
            log.debug("ray.train: could not fetch JobInfo metadata for %s", submission_id, exc_info=True)
        finally:
            done.set()

    t = threading.Thread(target=fetch, name="dd-ray-train-resolve-metadata", daemon=True)
    t.start()
    if not done.wait(timeout=_RUN_METADATA_TIMEOUT_S):
        log.warning(
            "ray.train: dashboard metadata fetch for %s exceeded %.1fs; proceeding without it",
            submission_id,
            _RUN_METADATA_TIMEOUT_S,
        )
        return {}
    return result


def _recover_parent_context(parent_headers: dict, kwargs: dict):
    """Three-channel context recovery. Order:
    wrapper headers → ``_dd_ray_trace_ctx`` kwarg → env. Returns the
    extracted ``Context`` or ``None`` when every channel is empty.
    Mutates ``kwargs`` to drop the transport-only ``_dd_ray_trace_ctx``
    entry so it is not forwarded to the user fn.
    """
    from ddtrace.contrib.internal.ray.constants import DD_RAY_TRACE_CTX  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.core.utils import _extract_tracing_context_from_env  # noqa: PLC0415
    from ddtrace.propagation.http import HTTPPropagator  # noqa: PLC0415
    from ddtrace.propagation.http import _TraceContext  # noqa: PLC0415

    if parent_headers:
        try:
            ctx = HTTPPropagator.extract(parent_headers)
            if ctx is not None and ctx.trace_id:
                return ctx
        except Exception:
            log.debug("ray.train: HTTPPropagator.extract failed", exc_info=True)

    kwarg_payload = kwargs.pop(DD_RAY_TRACE_CTX, None)
    if kwarg_payload:
        try:
            ctx = _TraceContext._extract(kwarg_payload)
            if ctx is not None and ctx.trace_id:
                return ctx
        except Exception:
            log.debug("ray.train: kwarg context extract failed", exc_info=True)

    try:
        env_ctx = _extract_tracing_context_from_env()
        if env_ctx is not None and env_ctx.trace_id:
            return env_ctx
    except Exception:
        log.debug("ray.train: env context extract failed", exc_info=True)

    return None


def _run_train_func_in_worker(
    fn: Callable[..., Any],
    training_job_id: str,
    parent_headers: dict,
    args: tuple,
    kwargs: dict,
    *,
    run_name: Optional[str] = None,
    submission_id: Optional[str] = None,
    run_metadata: Optional[dict] = None,
) -> Any:
    """Worker-side entry point: recover parent context, open the
    ``ray.train.worker`` span, register it with the long-running-span
    manager, run the user fn, and finish cleanly on success or exception.
    """
    from ddtrace.contrib.internal.ray.constants import RAY_SUBMISSION_ID_TAG  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import RAY_TRAIN_RANK_TAG  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import RAY_TRAIN_WORKER_OPERATION  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import RAY_TRAIN_WORLD_SIZE_TAG  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import TRAINING_JOB_ID_TAG  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.core.utils import _inject_context_in_env  # noqa: PLC0415
    from ddtrace.trace import tracer  # noqa: PLC0415

    # Capture the prior active context so we can restore it on exit and
    # use it as a last-resort parent if every other channel comes back
    # empty (e.g. Ray's actor-method wrap activated something for us).
    prior_active = tracer.context_provider.active()

    parent_ctx = _recover_parent_context(parent_headers, kwargs)
    if parent_ctx is None and prior_active is not None and getattr(prior_active, "trace_id", None):
        parent_ctx = prior_active
    if parent_ctx is None:
        log.debug("ray.train: no parent trace context recovered; worker span will be a trace root")
    else:
        tracer.context_provider.activate(parent_ctx)

    # Surface the driver-resolved job id to Layer 2/3 PyTorch emitters.
    # PyTorch may not be installed in every Ray environment; guard the import.
    # Capture the previous value so we can restore it when this worker fn
    # returns — Ray workers are reused across jobs, and a stale id would
    # mislabel spans from the next job on the same worker thread.
    # The driver-side wrapper already resolves training_job_id with the
    # right priority (explicit DD_PYTORCH_JOB_ID → Ray submission id →
    # env chain). Workers used to override unconditionally with their
    # own _RAY_SUBMISSION_ID lookup, which broke the user's explicit
    # DD_PYTORCH_JOB_ID override (workers picked submission_id while
    # the driver kept DD_PYTORCH_JOB_ID → spans landed under two
    # training_job.id values). Trust the driver's resolution and only
    # consult worker env as a last-resort fallback.
    import os as _os_w  # noqa: PLC0415

    if not training_job_id:
        fallback = _os_w.environ.get("_RAY_SUBMISSION_ID")
        if fallback:
            training_job_id = fallback.strip() or training_job_id
    effective_training_job_id = training_job_id
    worker_submission_id = _os_w.environ.get("_RAY_SUBMISSION_ID") or None
    if worker_submission_id:
        worker_submission_id = worker_submission_id.strip() or None

    previous_job_id = None
    previous_run_metadata: Optional[dict] = None
    try:
        from ddtrace.contrib.internal.pytorch._utils import get_cached_job_id  # noqa: PLC0415
        from ddtrace.contrib.internal.pytorch._utils import get_run_metadata_snapshot  # noqa: PLC0415
        from ddtrace.contrib.internal.pytorch._utils import set_cached_job_id  # noqa: PLC0415
        from ddtrace.contrib.internal.pytorch._utils import set_cached_run_metadata  # noqa: PLC0415

        previous_job_id = get_cached_job_id()
        previous_run_metadata = get_run_metadata_snapshot()
        set_cached_job_id(effective_training_job_id)
        set_cached_run_metadata(
            run_name=run_name,
            submission_id=worker_submission_id or submission_id,
            metadata=run_metadata or None,
        )
    except ImportError:
        log.debug("ray.train: pytorch contrib not available; skipping set_cached_job_id")

    rank = _resolve_rank()
    world_size = _resolve_world_size()

    per_rank_trace = bool(_ddconfig.ray.train_per_rank_trace)

    if per_rank_trace:
        # Open as a new trace root and link back to the fit span instead
        # of nesting under it. The entity backend joins traces via the
        # ``training_job.id`` tag.
        worker_span = tracer.start_span(RAY_TRAIN_WORKER_OPERATION, service=_ddconfig.ray.service)
        if parent_ctx is not None:
            try:
                worker_span.link_span(parent_ctx)
            except Exception:
                log.debug("ray.train: link_span failed in per_rank_trace mode", exc_info=True)
    elif parent_ctx is not None:
        worker_span = tracer.start_span(RAY_TRAIN_WORKER_OPERATION, child_of=parent_ctx, service=_ddconfig.ray.service)
    else:
        worker_span = tracer.start_span(RAY_TRAIN_WORKER_OPERATION, service=_ddconfig.ray.service)

    worker_span.set_tag(TRAINING_JOB_ID_TAG, effective_training_job_id)
    # Use the actual Ray submission id when we have it; fall back to the
    # driver-resolved training_job_id (may itself be the submission id, or
    # a user-supplied DD_PYTORCH_JOB_ID override).
    worker_span.set_tag(RAY_SUBMISSION_ID_TAG, worker_submission_id or submission_id or training_job_id)
    if rank is not None:
        worker_span.set_tag(RAY_TRAIN_RANK_TAG, str(rank))
        worker_span.resource = f"rank={rank}"
    if world_size is not None:
        worker_span.set_tag(RAY_TRAIN_WORLD_SIZE_TAG, str(world_size))

    # Tag local rank info (local_rank, node_rank, local_world_size).
    info = _resolve_local_rank_info()
    if "local_rank" in info:
        worker_span._set_attribute("ray.train.local_rank", info["local_rank"])
    if "node_rank" in info:
        worker_span._set_attribute("ray.train.node_rank", info["node_rank"])
    if "local_world_size" in info:
        worker_span._set_attribute("ray.train.local_world_size", info["local_world_size"])

    if run_name:
        worker_span.set_tag("ray.train.run_name", run_name)
    if run_metadata:
        for k, v in run_metadata.items():
            try:
                worker_span.set_tag(f"ray.metadata.{k}", str(v))
            except Exception:
                log.debug("ray.train: failed to set metadata tag %s", k, exc_info=True)

    try:
        _set_runtime_context_attributes(worker_span, submission_id=worker_submission_id or submission_id)
    except Exception:
        log.debug("ray.train: failed to attach runtime invariants to worker span", exc_info=True)

    try:
        start_long_running_span(worker_span)
    except Exception:
        log.warning("ray.train: start_long_running_span failed for worker span", exc_info=True)

    # Subprocesses spawned inside the user fn (NCCL launchers, torchrun
    # internals) inherit context via env vars.
    try:
        _inject_context_in_env(worker_span.context)
    except Exception:
        log.debug("ray.train: _inject_context_in_env failed", exc_info=True)

    tracer.context_provider.activate(worker_span)
    try:
        return fn(*args, **kwargs)
    except BaseException as exc:
        worker_span.set_exc_info(type(exc), exc, exc.__traceback__)
        raise
    finally:
        try:
            stop_long_running_span(worker_span)
        except Exception:
            log.debug("ray.train: stop_long_running_span failed; finishing span directly", exc_info=True)
            worker_span.finish()
        try:
            tracer.context_provider.activate(prior_active)
        except Exception:
            log.debug("ray.train: failed to restore prior active context (worker)", exc_info=True)
        try:
            from ddtrace.contrib.internal.pytorch._utils import set_cached_job_id  # noqa: PLC0415

            set_cached_job_id(previous_job_id)
        except ImportError:
            pass
        try:
            if previous_run_metadata is not None:
                from ddtrace.contrib.internal.pytorch._utils import restore_run_metadata_snapshot  # noqa: PLC0415

                restore_run_metadata_snapshot(previous_run_metadata)
        except Exception:
            log.debug("ray.train: failed to restore previous run metadata", exc_info=True)


_TRAIN_INSTALLED = False
# Module-level reference to the ModuleWatchdog hook callback so
# `_uninstall_train` can unregister it. Without this, repeated
# `install -> uninstall -> install` cycles stack callbacks on
# `ray.train.torch` and the wraps end up double-applied on the second
# install (codex re-patch regression).
_TRAIN_MODULE_HOOK = None


def _install_train() -> None:
    """Wrap ``ray.train.torch.TorchTrainer.__init__`` and ``.fit``.

    Registers a ``ModuleWatchdog`` hook on ``ray.train.torch`` so the
    wrap fires on the first import of that module even if Ray was
    imported before our contrib patch ran. The hook reference is held
    on the module global ``_TRAIN_MODULE_HOOK`` so ``_uninstall_train``
    can call ``ModuleWatchdog.unregister_module_hook`` and avoid
    callback-stacking across ``patch -> unpatch -> patch`` cycles.

    Idempotent: a second call before ``_uninstall_train`` is a no-op.
    """
    global _TRAIN_INSTALLED, _TRAIN_MODULE_HOOK
    if _TRAIN_INSTALLED:
        return

    from wrapt import wrap_function_wrapper as _w  # noqa: PLC0415

    from ddtrace.internal.module import ModuleWatchdog  # noqa: PLC0415

    def _hook(m):
        _w(m.TorchTrainer, "__init__", _wrapped_torch_trainer_init)
        _w(m.TorchTrainer, "fit", _wrapped_torch_trainer_fit)

    ModuleWatchdog.register_module_hook("ray.train.torch", _hook)
    _TRAIN_MODULE_HOOK = _hook
    _TRAIN_INSTALLED = True


def _uninstall_train() -> None:
    """Reverse the wraps installed by :func:`_install_train`. Safe to call
    when ``ray.train.torch`` was never imported or ``_install_train`` was
    never called (no-op in those cases).
    """
    global _TRAIN_INSTALLED, _TRAIN_MODULE_HOOK
    if not _TRAIN_INSTALLED:
        return

    from ddtrace.contrib.internal.trace_utils import unwrap as _u  # noqa: PLC0415
    from ddtrace.internal.module import ModuleWatchdog  # noqa: PLC0415

    # Unregister the ModuleWatchdog callback so subsequent `_install_train`
    # calls do not stack a second callback on top of an inherited one
    # (which would double-wrap on the next `ray.train.torch` import).
    if _TRAIN_MODULE_HOOK is not None:
        try:
            ModuleWatchdog.unregister_module_hook("ray.train.torch", _TRAIN_MODULE_HOOK)
        except Exception:
            log.debug("ray.train: failed to unregister ModuleWatchdog hook", exc_info=True)
        _TRAIN_MODULE_HOOK = None

    try:
        import ray.train.torch  # noqa: PLC0415

        _u(ray.train.torch.TorchTrainer, "__init__")
        _u(ray.train.torch.TorchTrainer, "fit")
    except Exception:
        log.debug("ray.train: _uninstall_train no-op (module missing or never wrapped)", exc_info=True)
    finally:
        _TRAIN_INSTALLED = False


def _wrapped_torch_trainer_init(wrapped, instance, args, kwargs):
    """Replace the user's ``train_loop_per_worker`` with a picklable
    wrapper instance.

    Resolution of the training job id happens here so a single id is
    captured for the lifetime of the trainer; the wrapper instance
    carries it across the cloudpickle boundary into workers.

    Never crashes the user's ``TorchTrainer(...)`` call: any failure
    inside our wrap logic falls through to the original constructor with
    the user's fn untouched.
    """
    try:
        if not _ddconfig.ray.train_enabled:
            return wrapped(*args, **kwargs)

        if "train_loop_per_worker" not in kwargs:
            # TorchTrainer accepts positional too, but ``ray.train``
            # documents it as keyword-only in practice. Pass through
            # rather than risk mis-substituting the wrong positional.
            return wrapped(*args, **kwargs)

        user_fn = kwargs["train_loop_per_worker"]
        if not callable(user_fn):
            return wrapped(*args, **kwargs)

        from ddtrace.contrib.internal.pytorch._utils import resolve_job_id_from_env  # noqa: PLC0415

        # Priority:
        #   1. ``DD_PYTORCH_JOB_ID`` — explicit operator override always wins.
        #   2. Ray ``RuntimeContext.get_submission_id()`` — preferred default
        #      under Ray Train so spans + the Ray dashboard agree on a single
        #      id without env plumbing.
        #   3. Existing env chain (RAY_JOB_ID, TORCHELASTIC_RUN_ID,
        #      KUBEFLOW_TRAINING_JOB_ID, SLURM_JOB_ID, …) → UUID fallback.
        training_job_id = env.get("DD_PYTORCH_JOB_ID") or None
        if training_job_id:
            training_job_id = training_job_id.strip() or None
        if not training_job_id:
            training_job_id = _resolve_training_job_id_from_ray()
        if not training_job_id:
            training_job_id = resolve_job_id_from_env()

        submission_id = _ray_submission_id_from_env()
        run_name = _resolve_run_name(instance)
        run_metadata = _resolve_run_metadata(submission_id)

        wrapper = _DDTrainFuncWrapper(user_fn, training_job_id=training_job_id)
        wrapper._run_name = run_name
        wrapper._submission_id = submission_id
        wrapper._run_metadata = run_metadata
        kwargs["train_loop_per_worker"] = wrapper

        # Cache the resolved values on the trainer instance so fit() can reuse them
        # without calling _resolve_run_metadata again.
        try:
            instance._dd_cached_run_metadata = run_metadata
            instance._dd_cached_submission_id = submission_id
            instance._dd_cached_run_name = run_name
        except Exception:
            log.debug("ray.train: failed to cache resolved run metadata on trainer", exc_info=True)
    except Exception:
        log.debug("ray.train: _wrapped_torch_trainer_init failed; passing through", exc_info=True)

    return wrapped(*args, **kwargs)


def _wrapped_torch_trainer_fit(wrapped, instance, args, kwargs):
    """Driver-side fit wrapper. Opens the ``ray.train.fit`` root span,
    binds W3C parent-context headers into the wrapper *before* Ray Train
    pickles it to workers, and tags ``training_job.status`` on
    completion or exception.

    Never crashes the user's ``trainer.fit()`` call: failures in our
    bookkeeping fall through to the original fit; user-side exceptions
    are re-raised unchanged after tagging.
    """
    if not _ddconfig.ray.train_enabled:
        return wrapped(*args, **kwargs)

    from ddtrace.contrib.internal.ray.constants import RAY_SUBMISSION_ID_TAG  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import RAY_TRAIN_FIT_OPERATION  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import RAY_TRAIN_WORLD_SIZE_TAG  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import TRAINING_JOB_FRAMEWORK_TAG  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import TRAINING_JOB_ID_TAG  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import TRAINING_JOB_STATUS_FAILED  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import TRAINING_JOB_STATUS_RUNNING  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import TRAINING_JOB_STATUS_SUCCEEDED  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import TRAINING_JOB_STATUS_TAG  # noqa: PLC0415
    from ddtrace.contrib.internal.ray.constants import TRAINING_JOB_TRAINER_TAG  # noqa: PLC0415
    from ddtrace.trace import tracer  # noqa: PLC0415

    # Ray's ``TorchTrainer.__init__`` stores the user fn as
    # ``self._train_loop_per_worker`` (underscore-prefixed). Probe the
    # private name first, fall back to the public one for forward
    # compatibility with Ray versions that may rename or expose it.
    wrapper = getattr(instance, "_train_loop_per_worker", None) or getattr(instance, "train_loop_per_worker", None)
    if isinstance(wrapper, _DDTrainFuncWrapper):
        training_job_id = wrapper._training_job_id
    else:
        # User passed a non-wrapped fn (init wrapping was skipped or
        # failed). Still emit the fit span so the entity has an anchor.
        try:
            from ddtrace.contrib.internal.pytorch._utils import resolve_job_id_from_env  # noqa: PLC0415

            training_job_id = env.get("DD_PYTORCH_JOB_ID") or None
            if training_job_id:
                training_job_id = training_job_id.strip() or None
            if not training_job_id:
                training_job_id = _resolve_training_job_id_from_ray()
            if not training_job_id:
                training_job_id = resolve_job_id_from_env()
        except Exception:
            log.debug("ray.train: could not resolve training_job_id at fit", exc_info=True)
            return wrapped(*args, **kwargs)

    scaling = getattr(instance, "_scaling_config", None) or getattr(instance, "scaling_config", None)
    world_size = getattr(scaling, "num_workers", None) if scaling is not None else None

    # Driver-side context for the human-readable tags.
    # Check cache first (populated by __init__) to avoid redundant _resolve_run_metadata call.
    cached_run_metadata = getattr(instance, "_dd_cached_run_metadata", None)
    cached_submission_id = getattr(instance, "_dd_cached_submission_id", None)
    cached_run_name = getattr(instance, "_dd_cached_run_name", None)

    submission_id = cached_submission_id if cached_submission_id is not None else _ray_submission_id_from_env()
    run_name = cached_run_name if cached_run_name is not None else _resolve_run_name(instance)
    run_metadata = cached_run_metadata if cached_run_metadata is not None else _resolve_run_metadata(submission_id)

    # Recover the trace context the dashboard-side ray.job span injected
    # via _RAY_TRACE_CTX env vars before launching the driver entrypoint.
    # When present, ray.train.fit becomes a child of ray.job so the full
    # submit → fit → workers → pytorch.rank tree lives in a single trace.
    # When absent (job started outside our dashboard wrapper, e.g. direct
    # API call), fit_span stays a trace root.
    parent_ctx = None
    try:
        from ddtrace.contrib.internal.ray.core.utils import _extract_tracing_context_from_env  # noqa: PLC0415

        env_ctx = _extract_tracing_context_from_env()
        if env_ctx is not None and getattr(env_ctx, "trace_id", None):
            parent_ctx = env_ctx
    except Exception:
        log.debug("ray.train: env context extract failed at fit", exc_info=True)

    if parent_ctx is not None:
        fit_span = tracer.start_span(RAY_TRAIN_FIT_OPERATION, child_of=parent_ctx, service=_ddconfig.ray.service)
    else:
        fit_span = tracer.start_span(RAY_TRAIN_FIT_OPERATION, service=_ddconfig.ray.service)
    # Pin USER_KEEP: without this, head-based sampling can drop the fit
    # span even though its USER_KEEP-tagged children (pytorch.rank etc.)
    # survive — leaving orphan child spans whose parent_id points to a
    # nowhere span.
    try:
        from ddtrace.constants import USER_KEEP  # noqa: PLC0415

        fit_span.context.sampling_priority = USER_KEEP
    except Exception:
        log.debug("ray.train: failed to pin fit_span sampling_priority", exc_info=True)
    fit_span.set_tag("manual.keep")
    fit_span.set_tag(TRAINING_JOB_ID_TAG, training_job_id)
    fit_span.set_tag(RAY_SUBMISSION_ID_TAG, submission_id or training_job_id)
    fit_span.set_tag(TRAINING_JOB_FRAMEWORK_TAG, "torch")
    fit_span.set_tag(TRAINING_JOB_TRAINER_TAG, "TorchTrainer")
    fit_span.set_tag(TRAINING_JOB_STATUS_TAG, TRAINING_JOB_STATUS_RUNNING)
    if world_size is not None:
        fit_span.set_tag(RAY_TRAIN_WORLD_SIZE_TAG, str(world_size))
        # Also emit as a numeric facet so the spans aggregate API can read
        # world_size directly off ray.train.fit (e.g. max(@world_size) grouped
        # by submission id), avoiding a bridge through pytorch.rank.
        try:
            fit_span._set_attribute("world_size", int(world_size))
        except Exception:
            log.debug("ray.train: failed to set numeric world_size on fit span", exc_info=True)
    if run_name:
        fit_span.set_tag("ray.train.run_name", run_name)
    if run_metadata:
        for k, v in run_metadata.items():
            try:
                fit_span.set_tag(f"ray.metadata.{k}", str(v))
            except Exception:
                log.debug("ray.train: failed to set fit metadata tag %s", k, exc_info=True)

    # Also publish into the pytorch run-metadata cache so the driver-side
    # rank-root (if pytorch ddtrace.auto loaded here too) and any later
    # worker call paths read the same values.
    try:
        from ddtrace.contrib.internal.pytorch._utils import set_cached_run_metadata  # noqa: PLC0415

        set_cached_run_metadata(
            run_name=run_name,
            submission_id=submission_id,
            metadata=run_metadata or None,
        )
    except ImportError:
        pass

    _tag_ray_train_config(fit_span, instance)

    if _ddconfig.ray.train_per_rank_trace:
        from ddtrace.contrib.internal.ray.constants import RAY_TRAIN_TRACE_MODE_PER_RANK  # noqa: PLC0415
        from ddtrace.contrib.internal.ray.constants import RAY_TRAIN_TRACE_MODE_TAG  # noqa: PLC0415

        fit_span.set_tag(RAY_TRAIN_TRACE_MODE_TAG, RAY_TRAIN_TRACE_MODE_PER_RANK)

    if isinstance(wrapper, _DDTrainFuncWrapper):
        # Construct a fresh wrapper with headers already bound and swap it
        # in. Mutating the existing instance does not survive Ray Train's
        # cloudpickle pipeline (Ray appears to snapshot the wrapper before
        # `fit()` runs); replacing the reference forces Ray to pickle the
        # already-bound instance.
        new_wrapper = _DDTrainFuncWrapper(wrapper._fn, training_job_id=wrapper._training_job_id)
        # AIDEV-NOTE: Copy human-readable metadata from the __init__-time
        # wrapper. Forgetting this causes workers to see None for
        # `_run_name` / `_submission_id` / `_run_metadata` and the
        # ray.train.run_name / ray.metadata.* tags vanish on worker spans.
        new_wrapper._run_name = wrapper._run_name
        new_wrapper._submission_id = wrapper._submission_id
        new_wrapper._run_metadata = dict(wrapper._run_metadata) if wrapper._run_metadata else {}
        new_wrapper.bind_parent_context(fit_span.context)
        # Best-effort swap on every place Ray Train might read from when it
        # serializes the trainer for workers.
        try:
            instance._train_loop_per_worker = new_wrapper
        except Exception:
            log.debug("ray.train: failed to swap _train_loop_per_worker", exc_info=True)
        param_dict = getattr(instance, "_param_dict", None)
        if isinstance(param_dict, dict) and "train_loop_per_worker" in param_dict:
            param_dict["train_loop_per_worker"] = new_wrapper

    try:
        _set_runtime_context_attributes(fit_span, submission_id=submission_id)
    except Exception:
        log.debug("ray.train: failed to attach runtime invariants to fit span", exc_info=True)

    try:
        start_long_running_span(fit_span)
    except Exception:
        log.warning("ray.train: start_long_running_span failed for fit span", exc_info=True)

    prior_active = tracer.context_provider.active()
    tracer.context_provider.activate(fit_span)
    try:
        result = wrapped(*args, **kwargs)
        fit_span.set_tag(TRAINING_JOB_STATUS_TAG, TRAINING_JOB_STATUS_SUCCEEDED)
        return result
    except BaseException as exc:
        fit_span.set_tag(TRAINING_JOB_STATUS_TAG, TRAINING_JOB_STATUS_FAILED)
        fit_span.set_exc_info(type(exc), exc, exc.__traceback__)
        raise
    finally:
        try:
            stop_long_running_span(fit_span)
            # Ray entrypoint exits immediately after fit returns; without a
            # synchronous flush the writer can be interrupted before the
            # fit_span ships, leaving its USER_KEEP children orphaned.
            try:
                tracer.flush()
            except Exception:
                log.debug("ray.train: tracer.flush after fit failed", exc_info=True)
        except Exception:
            log.debug("ray.train: stop_long_running_span failed for fit span; finishing span directly", exc_info=True)
            fit_span.finish()
        try:
            tracer.context_provider.activate(prior_active)
        except Exception:
            log.debug("ray.train: failed to restore prior active context (fit)", exc_info=True)
