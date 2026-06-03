# PyTorch L0 Rank-Span Extraction Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Create a standalone `kr-igor/pytorch-rank-span` PR (off `main`) containing only the always-on `pytorch.rank` lifetime span — no collective wrappers, no metrics, no summary, no hooks.

**Architecture:** Pull each file from `kr-igor/pytorch-experiments` via `git restore --source`, then surgically delete non-L0 sections. The result is seven small, focused files that compile and test cleanly against `main` with no new user-facing knobs.

**Tech Stack:** Python, `wrapt` (monkey-patching), `ddtrace` tracer/spans, `torch.distributed`

---

### Task 1: Create branch from main

**Files:**
- (branch setup only)

- [ ] **Step 1: Verify you are on the experiments branch and it is clean**

```bash
git status
git log --oneline -3
```

Expected: branch `kr-igor/pytorch-experiments`, no uncommitted changes.

- [ ] **Step 2: Create and switch to the new branch**

```bash
git checkout main
git checkout -b kr-igor/pytorch-rank-span
```

Expected: `Switched to a new branch 'kr-igor/pytorch-rank-span'`

- [ ] **Step 3: Verify clean state off main**

```bash
git log --oneline -3
python -c "import ddtrace; print('ok')"
```

Expected: recent `main` commits, no pytorch-related files yet.

---

### Task 2: Pull and strip `__init__.py`

**Files:**
- Create: `ddtrace/contrib/internal/pytorch/__init__.py`

The experiments `__init__.py` has a large docstring covering all layers and many `config._add` keys. We strip it to L0-only.

- [ ] **Step 1: Pull the file from the experiments branch**

```bash
git restore --source kr-igor/pytorch-experiments -- ddtrace/contrib/internal/pytorch/__init__.py
```

- [ ] **Step 2: Replace the file content with the L0-only version**

Open `ddtrace/contrib/internal/pytorch/__init__.py` and replace the entire contents with:

```python
"""
The pytorch integration traces PyTorch distributed training jobs.

Always-on: a single long-lived ``pytorch.rank`` span is emitted per rank.
Tags: ``rank``, ``world_size``, ``framework`` (DDP / FSDP / DeepSpeed),
``launcher``, ``torch.distributed.backend``, ``training_job_id``
(auto-resolved from ``RAY_JOB_ID``, ``TORCHELASTIC_RUN_ID``,
``KUBEFLOW_TRAINING_JOB_ID``, ``SLURM_JOB_ID``, or a per-rank UUID),
and Ray Train run context when running under Ray Train.


Enabling
~~~~~~~~

The PyTorch integration is **opt-in**. Enable explicitly via::

    DD_PATCH_MODULES=pytorch:true

or programmatically::

    import ddtrace
    ddtrace.patch(pytorch=True)


Global configuration
~~~~~~~~~~~~~~~~~~~~

.. py:data:: ddtrace.config.pytorch["service"]

   The service name reported by default for pytorch spans.

   This option can also be set with the ``DD_PYTORCH_SERVICE`` environment variable.

   Default: ``"pytorch"``

"""

from ddtrace import config
from ddtrace.internal.settings import env


# AIDEV-NOTE: Pop first — importlib.reload otherwise keeps stale env defaults.
config._integration_configs.pop("pytorch", None)
config._add(
    "pytorch",
    {
        "_default_service": "pytorch",
        "service": env.get("DD_PYTORCH_SERVICE"),
    },
)
```

- [ ] **Step 3: Verify it imports cleanly**

```bash
python -c "import ddtrace.contrib.internal.pytorch; print('ok')"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add ddtrace/contrib/internal/pytorch/__init__.py
git commit -m "feat(pytorch): add pytorch integration module (L0 config only)"
```

---

### Task 3: Pull `_device.py` and `_test_helpers.py` as-is

**Files:**
- Create: `ddtrace/contrib/internal/pytorch/_device.py`
- Create: `ddtrace/contrib/internal/pytorch/_test_helpers.py`

Both files are pulled unchanged — `_device.py` provides device/FLOPS discovery used by `_build_span`; `_test_helpers.py` provides test utilities.

- [ ] **Step 1: Pull both files**

```bash
git restore --source kr-igor/pytorch-experiments -- \
    ddtrace/contrib/internal/pytorch/_device.py \
    ddtrace/contrib/internal/pytorch/_test_helpers.py
```

- [ ] **Step 2: Verify they import cleanly**

```bash
python -c "from ddtrace.contrib.internal.pytorch import _device; print('ok')"
python -c "from ddtrace.contrib.internal.pytorch import _test_helpers; print('ok')"
```

Expected: `ok` for each.

- [ ] **Step 3: Commit**

```bash
git add ddtrace/contrib/internal/pytorch/_device.py \
        ddtrace/contrib/internal/pytorch/_test_helpers.py
git commit -m "feat(pytorch): add device discovery and test helpers"
```

---

### Task 4: Pull and strip `_utils.py`

**Files:**
- Create: `ddtrace/contrib/internal/pytorch/_utils.py`

The experiments `_utils.py` contains collective/summary helpers, AMP-skip state, CUDA event decision cache, and clock-offset computation. None of these are needed for L0.

**Keep:** run-metadata cache, job_id helpers (`set_cached_job_id`, `get_cached_job_id`, `resolve_job_id_from_env`, `job_id_env_set`), `set_training_job_id_tag`, `get_rank`, `register_framework`, `_FRAMEWORK_REGISTRY`, `_get_active_framework`, `_active_stack`, `_stack`, fork-safe `_reset_child_state`.

**Drop:** `_amp_skip_state`, `is_amp_step_in_progress`, `_LAST_OPTIMIZER_STEP_END_NS`, `get_last_optimizer_step_end_ns`, `set_last_optimizer_step_end_ns`, `is_instrumentation_bypassed`, `_instrumentation_bypass`, `_clear_cuda_event_decision_cache`, `_should_record_cuda_event`, `_should_record_cuda_event_uncached`, `compute_clock_offset_ns`, `ClockOffset`, `_stack`, `_enter_framework`, `_active_stack`.

- [ ] **Step 1: Pull the file**

```bash
git restore --source kr-igor/pytorch-experiments -- ddtrace/contrib/internal/pytorch/_utils.py
```

- [ ] **Step 2: Delete the following functions/blocks entirely from `_utils.py`**

Delete these named items (each is a standalone function or module-level variable):
- `_LAST_OPTIMIZER_STEP_END_NS = threading.local()` and its comment block
- `_amp_skip_state = threading.local()` and its comment block
- `def is_amp_step_in_progress() -> bool:` (2 lines)
- `_CLOCK_OFFSET_SAMPLES = 5` and its surrounding comment
- `ClockOffset = ...` type alias
- `def get_last_optimizer_step_end_ns() -> int:`
- `def set_last_optimizer_step_end_ns(value_ns: int) -> None:`
- `def now_ns() -> int:`
- `def is_instrumentation_bypassed() -> bool:`
- `def _instrumentation_bypass():` (the context manager + its helper)
- `def _clear_cuda_event_decision_cache() -> None:`
- `def _should_record_cuda_event(group, tensor) -> bool:`
- `def _should_record_cuda_event_uncached(group, tensor) -> bool:`
- `def compute_clock_offset_ns(samples: int = ...) -> ClockOffset:`
- `_active_stack = threading.local()`
- `def _stack() -> list:`
- `def _enter_framework(instance):` (the `@contextlib.contextmanager` one)

Also remove unused imports after the deletions: `contextlib`, `collections`, `NamedTuple` (if present).

- [ ] **Step 3: Verify it imports cleanly and key symbols are present**

```bash
python -c "
from ddtrace.contrib.internal.pytorch._utils import (
    set_cached_run_metadata, get_cached_run_metadata,
    resolve_job_id_from_env, set_training_job_id_tag,
    register_framework, _get_active_framework,
)
print('ok')
"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add ddtrace/contrib/internal/pytorch/_utils.py
git commit -m "feat(pytorch): add L0 utils (job-id, run-metadata cache, framework registry)"
```

---

### Task 5: Pull and strip `_rank_root.py`

**Files:**
- Create: `ddtrace/contrib/internal/pytorch/_rank_root.py`

The experiments `_rank_root.py` has `_tag_collective_summary` which drains `_metrics` and `_summary`. Both modules are excluded from L0, so remove the function and its call site.

- [ ] **Step 1: Pull the file**

```bash
git restore --source kr-igor/pytorch-experiments -- ddtrace/contrib/internal/pytorch/_rank_root.py
```

- [ ] **Step 2: Remove `_tag_collective_summary` and its call**

Delete the entire `_tag_collective_summary` function (it starts with `def _tag_collective_summary(span) -> None:` and spans roughly 50 lines, ending before `def open(`).

Then in `close()`, find and delete the line:

```python
        _tag_collective_summary(span)
```

Also remove the `_metrics` import at the top of the file (a line like `from ddtrace.contrib.internal.pytorch import _metrics`).

The resulting `close()` body should look like:

```python
def close() -> None:
    """Finish the per-rank span. Safe to call when no span is open."""
    global _span, _atexit_registered
    with _lock:
        span = _span
        _span = None
        if _atexit_registered:
            try:
                atexit.unregister(close)
            except Exception:
                pass
            _atexit_registered = False
    if span is None:
        return
    try:
        _tag_ray_run_context(span)
        span.finish()
        flush_thread = threading.Thread(
            target=lambda: _safe_flush(tracer),
            name="dd-pytorch-rank-root-flush",
            daemon=True,
        )
        flush_thread.start()
    except Exception:
        log.exception("pytorch: rank-root span close failed")
```

- [ ] **Step 3: Verify it imports and key functions are present**

```bash
python -c "
from ddtrace.contrib.internal.pytorch._rank_root import (
    open, close, set_framework, retag_ray_run_context,
)
print('ok')
"
```

Expected: `ok`

- [ ] **Step 4: Verify `_tag_collective_summary` is gone**

```bash
grep -n "_tag_collective_summary\|from ddtrace.contrib.internal.pytorch import _metrics" \
    ddtrace/contrib/internal/pytorch/_rank_root.py
```

Expected: no output.

- [ ] **Step 5: Commit**

```bash
git add ddtrace/contrib/internal/pytorch/_rank_root.py
git commit -m "feat(pytorch): add pytorch.rank lifetime span (open/close/retag)"
```

---

### Task 6: Pull and strip `_distributed.py`

**Files:**
- Create: `ddtrace/contrib/internal/pytorch/_distributed.py`

This is the largest stripping task — the file goes from ~1700 lines to ~200. Keep only the process-group wrappers, bootstrap logic, framework-detection stubs, and install/uninstall.

- [ ] **Step 1: Pull the file**

```bash
git restore --source kr-igor/pytorch-experiments -- ddtrace/contrib/internal/pytorch/_distributed.py
```

- [ ] **Step 2: Replace the entire file with the stripped L0 version**

Write `ddtrace/contrib/internal/pytorch/_distributed.py` with exactly this content:

```python
"""L0 distributed-training bootstrap: wraps init/destroy_process_group to
open and close the pytorch.rank lifetime span."""

import threading
from typing import Optional

import torch
import wrapt

from ddtrace.contrib.internal.pytorch._utils import _get_active_framework
from ddtrace.contrib.internal.pytorch._utils import get_cached_job_id
from ddtrace.contrib.internal.pytorch._utils import job_id_env_set
from ddtrace.contrib.internal.pytorch._utils import register_framework
from ddtrace.contrib.internal.pytorch._utils import resolve_job_id_from_env
from ddtrace.contrib.internal.pytorch._utils import set_cached_job_id
from ddtrace.contrib.internal.pytorch._utils import set_training_job_id_tag
from ddtrace.contrib.internal.trace_utils import unwrap as _unwrap
from ddtrace.contrib.internal.trace_utils import wrap as _wrap
from ddtrace.internal.logger import get_logger
from ddtrace.internal.settings import env

log = get_logger(__name__)

_no_env_job_id_warned: bool = False
_installed: bool = False

_state: dict = {
    "bootstrapped": False,
    "job_id": None,
    "rank": 0,
    "world_size": 1,
}
_bootstrap_lock = threading.Lock()


def _reset_child_state() -> None:
    # AIDEV-NOTE: Mutate _state in place so by-reference imports see the reset.
    global _bootstrap_lock, _no_env_job_id_warned
    _state.clear()
    _state.update({"bootstrapped": False, "job_id": None, "rank": 0, "world_size": 1})
    _bootstrap_lock = threading.Lock()
    _no_env_job_id_warned = False


def _distributed_available() -> bool:
    try:
        return torch.distributed.is_available()
    except Exception:
        return False


def _get_cached_backend() -> Optional[str]:
    try:
        if _distributed_available() and torch.distributed.is_initialized():
            return str(torch.distributed.get_backend())
    except Exception:
        pass
    return None


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
    """Capture rank/world_size and resolve the shared job id from env.

    AIDEV-NOTE: No cross-rank broadcast. Cross-rank correlation requires an
    env-supplied id (RAY_JOB_ID, TORCHELASTIC_RUN_ID, KUBEFLOW_TRAINING_JOB_ID,
    SLURM_JOB_ID) — already set by every supported launcher. When none is
    resolved, training_job.id is intentionally left unset on spans so missing
    correlation is visible in the UI.
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

    from ddtrace.contrib.internal.pytorch import _device  # noqa: PLC0415
    from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

    try:
        _device.discover(local_rank=int(_state["rank"] or 0))
    except Exception:
        log.exception("pytorch: device discovery failed")

    try:
        _rank_root.open(
            rank=int(_state["rank"] or 0),
            world_size=int(_state["world_size"] or 1),
            framework=_get_active_framework() or "none",
            training_job_id=publishable_job_id,
        )
    except Exception:
        log.exception("pytorch: rank-root span open failed")


def _wrapped_init_process_group(wrapped, instance, args, kwargs):
    with _bootstrap_lock:
        already = _state["bootstrapped"]
        if not already:
            _state["bootstrapped"] = True
    result = wrapped(*args, **kwargs)
    if not already:
        try:
            _bootstrap_distributed()
        except Exception:
            log.exception("pytorch: bootstrap failed inside init_process_group wrapper")
    return result


def _wrapped_destroy_process_group(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

        _rank_root.close()
    except Exception:
        log.debug("pytorch: rank-root close raised in destroy_process_group wrapper", exc_info=True)
    return result


# ---------------------------------------------------------------------------
# Framework-detection stubs: update the framework tag on the live rank span
# when the user wraps their model in DDP / FSDP / DeepSpeed.
# ---------------------------------------------------------------------------

def _wrapped_ddp_init(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    try:
        register_framework(instance, "ddp")
    except Exception:
        log.warning("pytorch: failed to register DDP framework", exc_info=True)
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


def _wrapped_fsdp_init(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    try:
        register_framework(instance, "fsdp")
    except Exception:
        log.warning("pytorch: failed to register FSDP framework", exc_info=True)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

        _rank_root.set_framework("fsdp")
    except Exception:
        log.debug("pytorch: failed to update rank-root framework tag", exc_info=True)
    return result


def _install_fsdp() -> None:
    try:
        from torch.distributed.fsdp import FullyShardedDataParallel  # noqa: F401
    except Exception:
        return
    _wrap("torch.distributed.fsdp", "FullyShardedDataParallel.__init__", _wrapped_fsdp_init)


def _uninstall_fsdp() -> None:
    try:
        from torch.distributed.fsdp import FullyShardedDataParallel
    except Exception:
        return
    try:
        _unwrap(FullyShardedDataParallel, "__init__")
    except Exception:
        log.debug("pytorch: failed to unwrap FSDP.__init__", exc_info=True)


def _wrapped_deepspeed_init(wrapped, instance, args, kwargs):
    result = wrapped(*args, **kwargs)
    try:
        register_framework(instance, "deepspeed")
    except Exception:
        log.warning("pytorch: failed to register deepspeed framework", exc_info=True)
    try:
        from ddtrace.contrib.internal.pytorch import _rank_root  # noqa: PLC0415

        _rank_root.set_framework("deepspeed")
    except Exception:
        log.debug("pytorch: failed to update rank-root framework tag", exc_info=True)
    return result


def _install_deepspeed() -> None:
    try:
        import deepspeed  # noqa: F401
    except Exception:
        return
    if not hasattr(deepspeed, "initialize"):
        return
    _wrap("deepspeed", "initialize", _wrapped_deepspeed_init)


def _uninstall_deepspeed() -> None:
    try:
        import deepspeed  # noqa: F401
    except Exception:
        return
    try:
        _unwrap(deepspeed, "initialize")
    except Exception:
        log.debug("pytorch: failed to unwrap deepspeed.initialize", exc_info=True)


# ---------------------------------------------------------------------------
# Install / uninstall
# ---------------------------------------------------------------------------

def install() -> None:
    global _installed
    if _installed:
        return
    if _distributed_available() and hasattr(torch.distributed, "init_process_group"):
        _wrap("torch.distributed", "init_process_group", _wrapped_init_process_group)
    if _distributed_available() and hasattr(torch.distributed, "destroy_process_group"):
        _wrap("torch.distributed", "destroy_process_group", _wrapped_destroy_process_group)
    _install_ddp()
    _install_fsdp()
    _install_deepspeed()
    _installed = True
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
    if not _installed:
        return
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
    _uninstall_deepspeed()
```

- [ ] **Step 3: Verify it imports cleanly**

```bash
python -c "
from ddtrace.contrib.internal.pytorch._distributed import (
    install, uninstall, _bootstrap_distributed, _wrapped_init_process_group,
)
print('ok')
"
```

Expected: `ok`

- [ ] **Step 4: Commit**

```bash
git add ddtrace/contrib/internal/pytorch/_distributed.py
git commit -m "feat(pytorch): add L0 distributed bootstrap (init/destroy wrappers + framework stubs)"
```

---

### Task 7: Pull and strip `patch.py`

**Files:**
- Create: `ddtrace/contrib/internal/pytorch/patch.py`

The experiments `patch.py` calls `shutdown_profiler` from `_profiler` in `unpatch()`. Drop that — no profiler in L0.

- [ ] **Step 1: Pull the file**

```bash
git restore --source kr-igor/pytorch-experiments -- ddtrace/contrib/internal/pytorch/patch.py
```

- [ ] **Step 2: Remove the profiler shutdown from `unpatch()`**

Find and delete this block in `unpatch()`:

```python
    # Tear down the Layer 3 profiler. Always called: `shutdown_profiler` is a
    # no-op when no profiler is running, so we don't gate on the current value
    # of DD_PYTORCH_KERNEL_PROFILING (which may have changed since patch()).
    try:
        from ddtrace.contrib.internal.pytorch._profiler import shutdown_profiler

        shutdown_profiler()
    except Exception:
        log.debug("pytorch: Layer 3 shutdown raised; suppressing", exc_info=True)
```

The resulting `unpatch()` should be:

```python
def unpatch() -> None:
    if not getattr(torch, "_datadog_patch", False):
        return
    torch._datadog_patch = False
    from ddtrace.contrib.internal.pytorch import _distributed

    _distributed.uninstall()
```

- [ ] **Step 3: Verify patch/unpatch round-trip**

```bash
python -c "
import ddtrace
ddtrace.patch(pytorch=True)
ddtrace.unpatch(pytorch=True)
print('ok')
"
```

Expected: `ok` with no errors.

- [ ] **Step 4: Commit**

```bash
git add ddtrace/contrib/internal/pytorch/patch.py
git commit -m "feat(pytorch): add patch/unpatch entry points"
```

---

### Task 8: Pull and strip test files

**Files:**
- Create: `tests/contrib/pytorch/conftest.py`
- Create: `tests/contrib/pytorch/test_rank_root.py`
- Create: `tests/contrib/pytorch/test_pytorch_patch.py`
- Create: `tests/contrib/pytorch/test_pytorch.py`
- Create: `tests/contrib/pytorch/test_fork_safety.py`
- Create: `tests/contrib/pytorch/test_repatch_and_exception_paths.py`
- Create: `tests/contrib/pytorch/test_utils.py`

- [ ] **Step 1: Pull all test files**

```bash
git restore --source kr-igor/pytorch-experiments -- \
    tests/contrib/pytorch/__init__.py \
    tests/contrib/pytorch/conftest.py \
    tests/contrib/pytorch/test_rank_root.py \
    tests/contrib/pytorch/test_pytorch_patch.py \
    tests/contrib/pytorch/test_pytorch.py \
    tests/contrib/pytorch/test_fork_safety.py \
    tests/contrib/pytorch/test_repatch_and_exception_paths.py \
    tests/contrib/pytorch/test_utils.py
```

- [ ] **Step 2: Strip `test_pytorch.py` to L0 tests only**

Delete any test functions that reference:
- `_metrics`, `_summary`, `_hooks`, `_profiler`
- `collective_trace_enabled`, `summary_profiling`, `DD_PYTORCH_PROFILING`
- `pytorch.step`, `pytorch.forward`, `pytorch.backward`, `pytorch.optimizer`, `pytorch.grad_comm`, `pytorch.<collective>`
- Any facets like `collective.all_reduce.*`, `step.*`, `train.*`, `memory.*`

Keep tests that verify:
- `pytorch.rank` span is emitted
- `rank`, `world_size`, `framework`, `training_job_id` tags
- Job ID auto-resolution from env

- [ ] **Step 3: Strip `test_fork_safety.py` to `_rank_root` globals only**

Delete test functions that reference `_metrics`, `_summary`, `_hooks`, `_distributed._state["resolver"]`, `_distributed._state["gpu_sample_rate"]`, or `_distributed._state["gpu_sample_count"]`.

Keep tests for `_rank_root._span` reset and `_distributed._state` reset (the fields that still exist: `bootstrapped`, `job_id`, `rank`, `world_size`).

- [ ] **Step 4: Strip `test_repatch_and_exception_paths.py` to init/destroy wrapper paths**

Delete test functions that reference:
- Layer 1/2/3 features, `collective_trace_enabled`, `summary_profiling`
- `_install_collectives`, `_install_fsdp_collectives`, `_install_tensor_backward`, `_install_grad_clip`, `_install_optimizer`
- `_layer2_installed`

Keep tests for:
- Repatch cycle (patch → unpatch → patch)
- Exception in `_bootstrap_distributed`
- Exception in `_wrapped_destroy_process_group`

- [ ] **Step 5: Verify test files parse without import errors**

```bash
python -c "
import ast, sys
for f in [
    'tests/contrib/pytorch/test_rank_root.py',
    'tests/contrib/pytorch/test_pytorch_patch.py',
    'tests/contrib/pytorch/test_pytorch.py',
    'tests/contrib/pytorch/test_fork_safety.py',
    'tests/contrib/pytorch/test_repatch_and_exception_paths.py',
    'tests/contrib/pytorch/test_utils.py',
]:
    ast.parse(open(f).read())
    print(f'OK: {f}')
"
```

Expected: `OK:` for each file.

- [ ] **Step 6: Commit**

```bash
git add tests/contrib/pytorch/
git commit -m "test(pytorch): add L0 rank-span tests"
```

---

### Task 9: Run the test suite

**Files:**
- (test execution only)

- [ ] **Step 1: Run tests using the run-tests skill**

Use the `run-tests` skill. Target the files:
- `tests/contrib/pytorch/test_rank_root.py`
- `tests/contrib/pytorch/test_pytorch_patch.py`
- `tests/contrib/pytorch/test_pytorch.py`
- `tests/contrib/pytorch/test_fork_safety.py`
- `tests/contrib/pytorch/test_repatch_and_exception_paths.py`
- `tests/contrib/pytorch/test_utils.py`

- [ ] **Step 2: Fix any failures**

Common failure patterns after stripping:
- Test references a symbol that was deleted → remove the test or update the import.
- Test patches a config key that no longer exists → remove the config mock.
- `_state` key missing (e.g. `"resolver"`, `"gpu_sample_rate"`) → remove the assertion.

- [ ] **Step 3: Re-run until all pass**

```
All tests should pass before proceeding to the release note.
```

---

### Task 10: Write release note

**Files:**
- Create: `releasenotes/notes/<auto-generated-slug>.yaml`

- [ ] **Step 1: Use the releasenote skill**

Use the `releasenote` skill. The content to capture:

```
feat(pytorch): add always-on pytorch.rank lifetime span for distributed training.

Instruments torch.distributed.init_process_group / destroy_process_group to
emit a single pytorch.rank span per rank. Tags: rank, world_size, framework
(DDP/FSDP/DeepSpeed), launcher, torch.distributed.backend, training_job_id
(auto-resolved from RAY_JOB_ID / TORCHELASTIC_RUN_ID / SLURM_JOB_ID / UUID),
and Ray Train run context (ray.train.run_name, ray.submission_id,
ray.metadata.*) when running under Ray Train.

No new configuration knobs. Enable with DD_PATCH_MODULES=pytorch:true.
```

---

### Task 11: Lint and final commit

**Files:**
- (lint only)

- [ ] **Step 1: Run the lint skill on all modified files**

Use the `lint` skill targeting:
```
ddtrace/contrib/internal/pytorch/
tests/contrib/pytorch/
```

- [ ] **Step 2: Fix any lint issues**

Address any import ordering, unused imports, line-length, or type-annotation issues reported.

- [ ] **Step 3: Commit lint fixes if any**

```bash
git add ddtrace/contrib/internal/pytorch/ tests/contrib/pytorch/
git commit -m "chore(pytorch): lint fixes for L0 rank-span PR"
```

- [ ] **Step 4: Final smoke test**

```bash
python -c "
import ddtrace
ddtrace.patch(pytorch=True)
print('patch ok')
ddtrace.unpatch(pytorch=True)
print('unpatch ok')
from ddtrace.contrib.internal.pytorch._rank_root import open, close, retag_ray_run_context
from ddtrace.contrib.internal.pytorch._utils import set_cached_run_metadata, resolve_job_id_from_env
print('imports ok')
"
```

Expected: all three `ok` lines.

- [ ] **Step 5: Verify no unintended files from experiments branch**

```bash
git diff main --name-only | sort
```

Expected files (no others):
```
ddtrace/contrib/internal/pytorch/__init__.py
ddtrace/contrib/internal/pytorch/_device.py
ddtrace/contrib/internal/pytorch/_distributed.py
ddtrace/contrib/internal/pytorch/_rank_root.py
ddtrace/contrib/internal/pytorch/_test_helpers.py
ddtrace/contrib/internal/pytorch/_utils.py
ddtrace/contrib/internal/pytorch/patch.py
releasenotes/notes/<slug>.yaml
tests/contrib/pytorch/__init__.py
tests/contrib/pytorch/conftest.py
tests/contrib/pytorch/test_fork_safety.py
tests/contrib/pytorch/test_pytorch.py
tests/contrib/pytorch/test_pytorch_patch.py
tests/contrib/pytorch/test_rank_root.py
tests/contrib/pytorch/test_repatch_and_exception_paths.py
tests/contrib/pytorch/test_utils.py
```
