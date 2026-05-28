# Data Collected — PyTorch + Ray Train Instrumentation

> Source of truth for spans, tags, and facets emitted by
> `ddtrace.patch(pytorch=True)` and `ddtrace.patch(ray=True)`.
> Reflects branch `kr-igor/pytorch-experiments` @ `26fe0ccae7`.
> Maintainers: update when changing the public tag/facet contract.

This document is intentionally a contract document: each table row is
the schema for one piece of UI. Implementation rationale lives in code
comments and AIDEV anchors; this file lists what is emitted, under
which conditions, and from which source site — nothing more.

## At a glance

- [Quick reference: tier matrix](#quick-reference)
- [Configuration env vars](#configuration)

Both integrations are **opt-in**. Neither `pytorch` nor `ray` is auto-
patched (`ddtrace/_monkey.py:113`/`:121`). Enable each via
`DD_PATCH_MODULES` or `ddtrace.patch(...)`.

PyTorch additionally requires a distributed-launcher signal to install
its wrappers; see `DD_PYTORCH_FORCE_INSTALL` below. Ray installs
unconditionally once patched.

---

## PyTorch

### Spans

| Operation | Tier | Default state |
|---|---|---|
| `pytorch.rank` | L0 | On (installed) |
| `pytorch.allreduce` / `pytorch.allgather` / `pytorch.broadcast` / `pytorch.reducescatter` / `pytorch.barrier` / `pytorch.allgather_into_tensor` / `pytorch.reducescatter_tensor` | L1 | Off (`DD_PYTORCH_COLLECTIVE_TRACE=true`) |
| `pytorch.grad_comm` | L1 | Off + requires user comm hook |
| `pytorch.step` / `pytorch.forward` / `pytorch.backward` / `pytorch.optimizer` / `pytorch.data_load` | L2 | Off (`DD_PYTORCH_PROFILING=true`) |
| `pytorch.kernel` | L3 | Off (`DD_PYTORCH_KERNEL_PROFILING=true`, requires L2) |

Tier semantics:

| Tier | Trigger | Always-on once enabled? |
|---|---|---|
| L0 | `ddtrace.patch(pytorch=True)` AND (`RANK`/`WORLD_SIZE` set OR `init_process_group` already called OR `DD_PYTORCH_FORCE_INSTALL=true`) | Yes — `pytorch.rank` and L0 DogStatsD metrics |
| Summary mode | `DD_PYTORCH_SUMMARY_PROFILING=true` (default) | Yes by default; feeds facets onto `pytorch.rank` |
| L1 | `DD_PYTORCH_COLLECTIVE_TRACE=true` | Off by default |
| L2 | `DD_PYTORCH_PROFILING=true` | Off by default; per-step spans |
| L3 | `DD_PYTORCH_KERNEL_PROFILING=true` (requires L2) | Off by default |

---

### `pytorch.rank`

**Tier:** L0 (always-on once installed)
**What it represents:** One long-lived span covering the entire
distributed-training lifetime of a single rank. Opens at
`torch.distributed.init_process_group` (or at `patch()` time when
distributed is already initialized) and finishes at the first of
`destroy_process_group`, explicit `unpatch()`, or process exit
(`atexit`).
**Frameworks:** Any PyTorch ≥ 2.0 distributed program (DDP / FSDP /
DeepSpeed / vanilla `torch.distributed`).
**Overhead:** Constant per process — span open ~30 µs, plus one drain
pass at close (bounded by reservoir count, not step count).
**Source:** `ddtrace/contrib/internal/pytorch/_rank_root.py` (`_build_span`, `_tag_collective_summary`, `open`, `close`).

`pytorch.rank` is the sole anchor for cross-rank correlation. It is
force-kept (`manual.keep`) so it survives base sampling.

#### Static rank-context tags (set once at span open)

| Tag | Type | Always present? | Notes / Source |
|---|---|---|---|
| `component` | string | Yes | Constant `"pytorch"` |
| `debug.level` | string | Yes | Constant `"0"` |
| `framework` | string | Yes | One of `ddp`, `fsdp`, `deepspeed`, `none`. Backfilled by DDP/FSDP/DeepSpeed `__init__` wrappers when those run after rank-root open. |
| `rank` | int | Yes (facet) | From `torch.distributed.get_rank()` |
| `world_size` | int | Yes (facet) | From `torch.distributed.get_world_size()` |
| `training_job.id` | string | Conditional | Present iff a job-id was resolved from `DD_PYTORCH_JOB_ID` / `RAY_JOB_ID` / `TORCHELASTIC_RUN_ID` / `KUBEFLOW_TRAINING_JOB_ID` / `SLURM_JOB_ID`. Absent + one-time WARNING otherwise. |
| `job_id` | string | Conditional | Legacy alias of `training_job.id`. Same gating. |
| `manual.keep` | tag (no value) | Yes | Force-keep marker; pins trace to USER_KEEP. |
| `device.id` | string | Conditional | GPU UUID (preferred via pynvml) or `<host>:cuda:<idx>` fallback. Present iff CUDA available. CPU: `<host>:cpu`. |
| `device.kind` | string | Yes | `"cuda"` or `"cpu"` |
| `host` | string | Yes | `socket.gethostname()` |
| `device.index` | int (facet) | Conditional | Resolved via `LOCAL_RANK` env → `ray.train.get_context().get_local_rank()` → `torch.cuda.current_device()`. Absent when none resolve. |
| `device.gpu.name` | string | Conditional | From `torch.cuda.get_device_properties().name`. CUDA only. |
| `device.gpu.compute_capability` | string | Conditional | `"<major>.<minor>"` (e.g. `"8.0"`). CUDA only. |
| `device.gpu.sm_count` | int (facet) | Conditional | From `multi_processor_count`. CUDA only. |
| `device.gpu.total_memory_bytes` | int (facet) | Conditional | From `total_memory`. CUDA only. |
| `device.gpu.driver_version` | string | Conditional | Via pynvml. Absent when pynvml unavailable. |

#### Torch / CUDA / cuDNN / NCCL invariants (set once at span open)

| Tag | Type | Present iff |
|---|---|---|
| `torch.version` | string | `torch.__version__` available |
| `torch.cuda.version` | string | `torch.version.cuda` not None |
| `torch.cuda.hip_version` | string | ROCm/HIP build |
| `torch.cuda.nccl_version` | string | NCCL initialised; format `"X.Y.Z"` |
| `torch.cudnn.enabled` | string (`"true"`/`"false"`) | `torch.backends.cudnn` available |
| `torch.cudnn.benchmark` | string (`"true"`/`"false"`) | same |
| `torch.cudnn.deterministic` | string (`"true"`/`"false"`) | same |
| `torch.cudnn.version` | int (facet) | `cudnn.version()` returns int |
| `torch.float32_matmul_precision` | string | `torch.get_float32_matmul_precision()` returns non-empty |
| `torch.mps.available` | string (`"true"`) | `torch.backends.mps.is_available()` |

#### NCCL + launcher env signals (set once at span open)

| Tag | Type | Env var |
|---|---|---|
| `nccl.debug` | string | `NCCL_DEBUG` |
| `nccl.socket_ifname` | string | `NCCL_SOCKET_IFNAME` |
| `nccl.ib_disable` | string | `NCCL_IB_DISABLE` |
| `nccl.p2p_disable` | string | `NCCL_P2P_DISABLE` |
| `nccl.algo` | string | `NCCL_ALGO` |
| `nccl.proto` | string | `NCCL_PROTO` |
| `nccl.async_error_handling` | string | `TORCH_NCCL_ASYNC_ERROR_HANDLING` |
| `device.cuda.visible_devices` | string | `CUDA_VISIBLE_DEVICES` |
| `pytorch.master_addr` | string | `MASTER_ADDR` |
| `pytorch.local_rank` | int (facet) | `LOCAL_RANK` |
| `pytorch.local_world_size` | int (facet) | `LOCAL_WORLD_SIZE` |
| `pytorch.group_rank` | int (facet) | `GROUP_RANK` |
| `pytorch.group_world_size` | int (facet) | `GROUP_WORLD_SIZE` |
| `pytorch.master_port` | int (facet) | `MASTER_PORT` |

Each is present iff the corresponding env var is set to a non-empty
value. Int facets are dropped silently when the value fails `int()`
coercion.

#### Detected launcher / backend

| Tag | Type | Present iff |
|---|---|---|
| `launcher` | string | One of `torchrun` / `ray` / `slurm` / `kubeflow` resolved from env (`TORCHELASTIC_RUN_ID`, `_RAY_SUBMISSION_ID`/`RAY_JOB_ID`, `SLURM_JOB_ID`, `KUBEFLOW_TRAINING_JOB_ID`). Absent when no signature matches. |
| `torch.distributed.backend` | string | `torch.distributed.is_initialized()` returned true and `get_backend()` succeeded (`nccl`/`gloo`/`mpi`). |

#### Ray run-context tags (idempotent, set at open and re-applied at close)

Source: `_rank_root._tag_ray_run_context` → reads `pytorch._utils.get_cached_run_metadata()` which is populated by the Ray Train worker wrapper.

| Tag | Type | Present iff |
|---|---|---|
| `ray.train.run_name` | string | `RunConfig.name` set on the user's `TorchTrainer` |
| `ray.submission_id` | string | `_RAY_SUBMISSION_ID` env present (i.e. `ray job submit`) |
| `ray.metadata.<k>` | string | Each k/v in the JobInfo metadata dict fetched via `JobSubmissionClient.get_job_info(submission_id).metadata` (best-effort, bounded by 2 s timeout). |

#### Collective summary facets (drained at span close — set on every `pytorch.rank` regardless of L1)

Source: `_metrics.summary_snapshot_and_reset` → `_rank_root._tag_collective_summary`. Reservoir capacity 1024 (Algorithm-R).

| Facet | Type | Present iff |
|---|---|---|
| `collective.<op>.p10_ms` | float | ≥1 sample observed for op `<op>` (e.g. `all_reduce`). |
| `collective.<op>.p50_ms` | float | same |
| `collective.<op>.p90_ms` | float | same |
| `collective.<op>.p99_ms` | float | same |
| `collective.<op>.n` | int | same (observed total, not buffer length) |
| `collective.p99_max_ms` | float | At least one op had p99 > 0. Max p99 across all ops. |
| `collective.ops_count` | int | Distinct op count with samples. |

`<op>` values include: `all_reduce`, `all_gather`, `broadcast`,
`reduce_scatter`, `barrier`, `all_gather_into_tensor`,
`reduce_scatter_tensor`, `grad_comm` (DDP custom comm hooks only).
Dots in op names are sanitised to underscores in facet keys.

#### Summary-mode training-metric facets — see [§ Summary-mode facets on `pytorch.rank`](#summary-mode-facets-on-pytorchrank) below.

#### Failure-mode tags

| Tag | Type | Set when |
|---|---|---|
| `error.message` / `error.stack` / `error.type` | string | (Standard ddtrace) — Not set on `pytorch.rank` directly; the rank span is opened/closed defensively and errors during open are swallowed with a debug log. |

---

### `pytorch.allreduce` / `pytorch.allgather` / `pytorch.broadcast` / `pytorch.reducescatter` / `pytorch.barrier` / `pytorch.allgather_into_tensor` / `pytorch.reducescatter_tensor`

**Tier:** L1 (`DD_PYTORCH_COLLECTIVE_TRACE=true`)
**What it represents:** One call to the corresponding
`torch.distributed.*` collective. Wall-clock duration is the span
duration; GPU-side duration (resolved asynchronously via CUDA events)
is added as a facet when CUDA + NCCL paths are detected.
**Frameworks:** Any program calling `torch.distributed.{all_reduce,
all_gather, broadcast, reduce_scatter, barrier,
all_gather_into_tensor, reduce_scatter_tensor}`.
**Overhead:** Layer-One opens a span + records two CUDA events per
call. Span open ~10 µs; CUDA event create + record ~5 µs; the
background resolver finishes the span ~5 ms later.
**Source:** `_distributed.py:_make_collective_wrapper`, `_install_collectives`, `_install_fsdp_collectives`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `debug.level` | string | Yes | Constant `"1"` |
| `framework` | string | Yes | `ddp` / `fsdp` / `deepspeed` / `none` (innermost active framework context) |
| `rank` | int (facet) | Yes | From `_state["rank"]` |
| `world_size` | int (facet) | Yes | From `_state["world_size"]` |
| `training_job.id` | string | Conditional | Same gating as on `pytorch.rank`. Set via `set_training_job_id_tag`. |
| `job_id` | string | Conditional | Legacy alias of `training_job.id`. |
| `manual.keep` | tag | Yes | Set by `set_training_job_id_tag` unconditionally. |
| `bytes` | int (facet) | Conditional | Tensor byte count (sum across nested list/tuple). Absent on `pytorch.barrier`. |
| `gpu.duration_ms` | float (facet) | Conditional | Present iff CUDA path, NCCL backend, and CUDA event resolver succeeded. Resolved on background thread. |
| `ray.submission_id` | string | Conditional | Same gating as on `pytorch.rank` (from cached run metadata). |
| `ray.metadata.job_name` | string | Conditional | From cached run metadata. |
| `error.type` / `error.message` / `error.stack` | string | On exception | Set when the wrapped collective raises. |
| `_dd.error_reason` | string | On CUDA event failure | One of `cuda_event_record_error`, `cuda_event_overflow`, `cuda_event_unresolved`, `cuda_event_query_error`, `cuda_event_elapsed_error`. |

**Configuration knobs:**
- `DD_PYTORCH_COLLECTIVE_TRACE` (`true`/`false`, default `false`) — gate.
- `DD_PYTORCH_COLLECTIVE_GPU_SAMPLE_RATE` (int, default `100`) — when L1 is OFF, 1-in-N collectives still record CUDA events to feed `collective.<op>.gpu_duration_ms.*` summary facets. Set to `0` to disable summary-mode GPU sampling. Has no effect on L1 spans.

---

### `pytorch.grad_comm`

**Tier:** L1 (`DD_PYTORCH_COLLECTIVE_TRACE=true`) + the user must
register a custom DDP comm hook via `model.register_comm_hook(state,
hook)`.
**What it represents:** One DDP gradient bucket's communication step
(the user-supplied hook execution wrapped around our timing layer).
**Frameworks:** DDP only. **Not emitted for DDP's default fused C++
allreduce path** — that path bypasses Python and our wrapper never
fires. The verification run at SHA `ad9ceab102` observed 0
`pytorch.grad_comm` spans for this reason (no custom hook was
registered).
**Overhead:** Per-bucket: span open + two reservoir pushes + one
`_metrics.record_collective`. ~25 µs per bucket. The chained hook
also incurs a `tracer.flush()` once at backward exit when any bucket
emitted a span.
**Source:** `_distributed.py:_make_chained_comm_hook`, `_wrapped_register_comm_hook`.

Layer-Zero metrics (`pytorch.collective.duration_ms` etc. with
`op:grad_comm`) and summary facets
(`grad_comm.bucket_duration_ms.*`, `grad_comm.bytes_per_bucket.*`)
emit regardless of `collective_trace_enabled`; only the per-bucket
span is gated on L1.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `debug.level` | string | Yes | Constant `"1"` |
| `framework` | string | Yes | Constant `"ddp"` |
| `rank` | int (facet) | Yes | |
| `training_job.id` / `job_id` | string | Conditional | Same gating as elsewhere. |
| `manual.keep` | tag | Yes | Set by `set_training_job_id_tag`. |
| `bytes` | int (facet) | Conditional | Sum of bucket gradient bytes; absent when introspection fails. |
| `ray.submission_id` / `ray.metadata.job_name` | string | Conditional | From cached run metadata. |

The span is opened with `child_of=_get_active_backward_ctx()` so it
nests under the live `pytorch.backward` span. When no backward is
active (DDP warm-up before first user backward) it becomes a fresh
trace root.

**Configuration knobs:**
- `DD_PYTORCH_GRAD_COMM` (`true`/`false`, default `true`) — gate for the chained hook. When `false`, user hooks run unwrapped.
- `DD_PYTORCH_COLLECTIVE_TRACE` — gates only the per-bucket span; Layer Zero metrics and summary feeds emit regardless.

---

### `pytorch.fsdp.*` / FSDP framework context

FSDP does not emit dedicated `pytorch.fsdp_*` spans. The wrapper
opens a framework context around `FullyShardedDataParallel.forward`
so that collectives emitted during the sharded forward inherit
`framework=fsdp`. The `__init__` wrap records the framework and
attaches Layer-2 hooks to the inner module.
**Source:** `_distributed.py:_wrapped_fsdp_init`, `_wrapped_fsdp_forward`, `_install_fsdp`.

Similarly, DeepSpeed wraps `forward`/`backward`/`step` on
`DeepSpeedEngine` only to push framework context onto the active
stack — no dedicated spans.
**Source:** `_distributed.py:_wrapped_deepspeed_init`, `_wrapped_deepspeed_method`, `_install_deepspeed`.

---

### `pytorch.step`

**Tier:** L2 (`DD_PYTORCH_PROFILING=true`)
**What it represents:** One training-loop iteration delimited by the
designated optimizer's `step()` call. Back-dated to the previous
optimizer-step end so `pytorch.data_load` does not start before its
parent.
**Frameworks:** Any optimizer subclass of `torch.optim.Optimizer`.
The "designated" optimizer is the first instance whose class name
matches `DD_PYTORCH_STEP_OPTIMIZER` (or the first to call `step()`
when unset).
**Overhead:** ~30 µs per step at L2.
**Source:** `_hooks.py:_ensure_step_open`, `_close_step`, `_maybe_close_step`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `component` | string | Yes | Constant `"pytorch"` |
| `debug.level` | string | Yes | Constant `"2"` |
| `rank` | int (facet) | Yes | |
| `step` | int (facet) | Conditional | Set on close iff `skipped=False`. Monotonically incremented by the designation lock. |
| `skipped` | bool | Conditional | `True` when GradScaler reported an AMP overflow on the designated optimizer. |
| `training_job.id` / `job_id` / `manual.keep` | — | Conditional | Via `set_training_job_id_tag`. |
| `ray.submission_id` / `ray.metadata.job_name` | string | Conditional | From cached run metadata. |

**Configuration knobs:**
- `DD_PYTORCH_PROFILING` (`true`/`false`, default `false`) — gate.
- `DD_PYTORCH_STEP_OPTIMIZER` (string, default unset) — class-name match for designation. When unset, the first instance to call `step()` is designated.

---

### `pytorch.forward`

**Tier:** L2
**What it represents:** One `nn.Module.forward()` invocation on the
top-level user model (DDP/FSDP/DeepSpeed inner module). Per-thread
stack so nested forwards are correctly siblinged.
**Frameworks:** Any user model whose top-level `forward` is hooked
via `register_forward_pre_hook` / `register_forward_hook`.
**Overhead:** ~5 µs per forward (open + close).
**Source:** `_hooks.py:_forward_pre_hook`, `_forward_hook`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `component` | string | Yes | `"pytorch"` |
| `debug.level` | string | Yes | `"2"` |
| `rank` | int (facet) | Yes | |
| `training_job.id` / `job_id` / `manual.keep` / `ray.submission_id` / `ray.metadata.job_name` | — | Conditional | Via `set_training_job_id_tag`. |
| `_dd.error_reason` | string | On stack overflow | `"forward_hook_leak"` set on stale spans when the per-thread stack exceeds 16 entries (leak detection — typically caused by a `forward` that raised before its matching `forward_hook` ran). |

The summary-mode forward timing (`step.forward_ms` reservoir push)
runs **whether or not** L2 is enabled — the hook itself attaches as
long as `DD_PYTORCH_PROFILING=true` OR `DD_PYTORCH_SUMMARY_PROFILING=true`
(default).

---

### `pytorch.backward`

**Tier:** L2
**What it represents:** One `Tensor.backward()` call. Carries the
active-backward context that `pytorch.grad_comm` reads as its parent.
**Frameworks:** Any program calling `tensor.backward()`.
**Overhead:** ~10 µs per backward (open + close + ctx publish/clear).
**Source:** `_distributed.py:_wrapped_tensor_backward` — sole emitter.
The former `_hooks._full_backward_hook` (registered via
`model.register_full_backward_hook`) has been removed; it emitted a
zero-duration duplicate span and carried no additional data.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `component` | string | Yes | `"pytorch"` |
| `debug.level` | string | Yes | `"2"` |
| `rank` | int (facet) | Yes | |
| `training_job.id` / `job_id` / `manual.keep` / `ray.submission_id` / `ray.metadata.job_name` | — | Conditional | |

The wrap also runs unconditionally when L1 is on (to drive the post-
backward `tracer.flush` that ships `pytorch.grad_comm` spans from the
CUDA-callback thread). In that case no `pytorch.backward` span is
opened, but `train.loss` and `step.backward_ms` reservoirs are still
fed when `DD_PYTORCH_SUMMARY_PROFILING=true` (default).

---

### `pytorch.optimizer`

**Tier:** L2
**What it represents:** One call to `optimizer.step()` on the
designated optimizer instance.
**Frameworks:** Any optimizer subclass of `torch.optim.Optimizer`.
**Overhead:** ~5 µs per step. No GPU sync.
**Source:** `_hooks.py:optimizer_step`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `component` | string | Yes | `"pytorch"` |
| `debug.level` | string | Yes | `"2"` |
| `optimizer` | string | Yes | Class name (`Adam`, `AdamW`, `SGD`, …). |
| `rank` | int (facet) | Yes | |
| `training_job.id` / `job_id` / `manual.keep` / `ray.submission_id` / `ray.metadata.job_name` | — | Conditional | |
| `error.type` / `error.message` / `error.stack` | string | On exception | Re-raised after tagging. |

Summary feeds `optim.learning_rate` (gauge, captured BEFORE the step)
and `step.optim_step_ms` (distribution) run in both L2 and summary-
only mode.

---

### `pytorch.data_load`

**Tier:** L2
**What it represents:** The gap between the previous optimizer step
end and the first forward of the new step (i.e., wall-clock time
spent fetching the next batch from the user's DataLoader). Emitted at
most once per `pytorch.step`. Skipped on the first iteration (no
previous step end yet).
**Frameworks:** Independent of framework — only requires that
`torch.optim.Optimizer.step` was wrapped (any L2-installed run).
**Overhead:** ~5 µs per step.
**Source:** `_hooks.py:_emit_data_load_span_if_needed`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `component` | string | Yes | `"pytorch"` |
| `debug.level` | string | Yes | `"2"` |
| `rank` | int (facet) | Yes | |
| `training_job.id` / `job_id` / `manual.keep` / `ray.submission_id` / `ray.metadata.job_name` | — | Conditional | |

---

### `pytorch.kernel`

**Tier:** L3 (`DD_PYTORCH_KERNEL_PROFILING=true`, requires
`DD_PYTORCH_PROFILING=true`)
**What it represents:** One CUDA kernel execution event observed by
`torch.profiler`, attributed to the `pytorch.step` whose window
contains the kernel's start timestamp.
**Frameworks:** Any CUDA-enabled PyTorch program with
`torch.profiler` available (CPU-only torch is unsupported).
**Overhead:** Bounded by the `torch.profiler` schedule
(`wait`/`warmup`/`active` defaults `99`/`1`/`5`). Each active window
captures `active` steps' kernels, then the profiler skips `wait`
steps before the next warmup.
**Source:** `_profiler.py:_emit_kernel_span`, `_on_trace_ready`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `debug.level` | string | Yes | Constant `"3"` |
| `kernel.name` | string | Yes | From `event.name` (e.g. `aten::cudaLaunchKernel`, `volta_sgemm_…`). |
| `stream_id` | string | Conditional | Present iff `event.stream` is not None. |
| `step` | int (facet) | Yes | Step number from the originating `pytorch.step` ring-buffer entry. |
| `rank` | int (facet) | Yes | |
| `duration_ms` | float (facet) | Yes | `(end_ns - start_ns) / 1e6` post-anchoring. |
| `flops` | float (facet) | Conditional | Present iff `event.flops` is truthy (varies by kernel). |
| `training_job.id` / `job_id` / `manual.keep` / `ray.submission_id` / `ray.metadata.job_name` | — | Conditional | Via `set_training_job_id_tag`. |

The span is force-kept (`sampling_priority=USER_KEEP`) on the
inherited Context so it survives even though its parent
(`pytorch.step`) has already finished and flushed by the time the
kernel batch arrives.

**Configuration knobs:**
- `DD_PYTORCH_KERNEL_PROFILING` (`true`/`false`, default `false`).
- `DD_PYTORCH_PROFILE_WAIT_STEPS` (int, default `99`).
- `DD_PYTORCH_PROFILE_WARMUP_STEPS` (int ≥1, default `1`).
- `DD_PYTORCH_PROFILE_ACTIVE_STEPS` (int ≥1, default `5`).
- `DD_PYTORCH_PROFILE_OFFSET_REFRESH_SEC` (int, default `600`) — clock-offset refresh cadence (currently no-op in production; the per-batch offset is derived from the ring buffer's latest step end).

---

## Summary-mode facets on `pytorch.rank`

When `DD_PYTORCH_SUMMARY_PROFILING=true` (the default), per-step
training metrics are recorded into in-process reservoirs and drained
into numeric facets on `pytorch.rank` at process exit
(`destroy_process_group` / explicit `unpatch` / `atexit`).

Three reservoir kinds, each with a fixed facet-key suffix shape:

- **Distribution** — Algorithm-R sampled (capped at 1024 samples), drains to
  `{count,min,max,mean,p10,p50,p90,p99}`. Duration metrics get the
  `_ms` suffix on min/max/mean/p* (not on `count`).
- **Gauge** — first/last/min/max/running mean, no samples retained.
  Drains to `{first,last,min,max,mean,count}`.
- **Counter** — monotonic int. Drains to `<name>_count`.

Empty reservoirs contribute no facets. Each facet listed below is
present iff at least one sample was recorded.

### Step-component durations (distributions)

| Reservoir key | Facet prefix | What it measures | Source site |
|---|---|---|---|
| `step.duration_ms` | `step.duration_ms.{count,min_ms,max_ms,mean_ms,p10_ms,p50_ms,p90_ms,p99_ms}` | Wall-clock between successive designated-optimizer step ends | `_summary.close_step_to_summary` (`_summary.py:662`) |
| `step.forward_ms` | `step.forward_ms.{count,min_ms,…,p99_ms}` | Sum of all `forward()` calls within one step | `_summary.close_step_to_summary` (`_summary.py:669`); accumulator fed by `_hooks._forward_pre_hook`/`_forward_hook` |
| `step.backward_ms` | `step.backward_ms.{count,…}` | One `Tensor.backward()` call duration | `_summary.close_step_to_summary` (`_summary.py:671`); accumulator fed by `_distributed._wrapped_tensor_backward` |
| `step.optim_step_ms` | `step.optim_step_ms.{count,…}` | `optimizer.step()` call duration | `_summary.close_step_to_summary` (`_summary.py:673`); accumulator fed by `_hooks.optimizer_step` |
| `step.data_fetch_ms` | `step.data_fetch_ms.{count,…}` | Gap from previous step end to first forward of new step | `_summary.close_step_to_summary` (`_summary.py:675`); accumulator fed by `_hooks._forward_pre_hook` |
| `step.grad_clip_ms` | `step.grad_clip_ms.{count,…}` | `torch.nn.utils.clip_grad_norm_` duration | `_summary.close_step_to_summary` (`_summary.py:677`); accumulator fed by `_distributed._wrapped_clip_grad_norm`. **Emits only when the training loop calls `clip_grad_norm_`.** |

### Training-quality distributions

| Reservoir key | Facet prefix | What it measures | Source site |
|---|---|---|---|
| `train.loss` | `train.loss.{count,min,max,mean,p10,p50,p90,p99}` (no `_ms`) | `loss.item()` per backward | `_distributed._wrapped_tensor_backward` (`_distributed.py:1320`). **Gated on `DD_PYTORCH_CAPTURE_LOSS=true` (default true). Forces GPU→host sync.** |
| `train.grad_norm` | `train.grad_norm.{count,…}` | Return value of `clip_grad_norm_` | `_distributed._wrapped_clip_grad_norm` (`_distributed.py:1418`). Emits only when user calls `clip_grad_norm_`. |
| `train.tflops` | `train.tflops.{count,…}` | Computed achieved TFLOPS — Chinchilla approximation `6 × active_param_count + 12 × layers × seq_len × hidden_dim` per token, divided by step seconds, divided by 1e12 | `_summary.close_step_to_summary` (`_summary.py:712`). Requires transformer-shaped model, `tokens_this_step > 0`, `step_ms > 0`. Gated on `DD_PYTORCH_MFU_ENABLED=true` (default). |
| `train.mfu` | `train.mfu.{count,…}` | `achieved_flops / peak_flops`, capped at 1.0 | `_summary.close_step_to_summary` (`_summary.py:721`). Requires matched `(gpu_name, dtype)` entry in the peak-FLOPS lookup table (`_device._PEAK_FLOPS_TABLE`). |
| `train.mfu_raw` | `train.mfu_raw.{count,…}` | Same as `train.mfu` but **not** capped at 1.0; for diagnosis. | `_summary.py:719` |
| `train.mfu_exclude_dataload` | `train.mfu_exclude_dataload.{count,…}` | MFU computed with compute time = step_ms − data_fetch_ms, capped at 1.0 | `_summary.py:740`. Requires `0 < data_fetch_ms < step_ms`. |
| `train.mfu_exclude_dataload_raw` | `train.mfu_exclude_dataload_raw.{count,…}` | Same uncapped. | `_summary.py:739` |
| `train.avg_dropped_tokens` | `train.avg_dropped_tokens.{count,…}` | `(routed_tokens − total_input_tokens) / total_input_tokens` averaged across MoE layers per step | `_summary.py:747`. Requires detected MoE modules (one of `MoE`/`MOELayer`/`TopKMoE`/`TopKGating`/`SwitchTransformerLayer`/`MoELayer`) with readable `exp_counts`/`expert_counts`/`tokens_per_expert` AND `input_token_count`/`n_tokens`/`tokens_seen`. |

### Optimiser + memory gauges

| Reservoir key | Facet prefix | What it measures | Source site |
|---|---|---|---|
| `optim.learning_rate` | `optim.learning_rate.{first,last,min,max,mean,count}` | `optimizer.param_groups[0]["lr"]` captured before each step runs | `_hooks.optimizer_step` (`_hooks.py:394`) |
| `memory.gpu_used_memory_bytes` | `memory.gpu_used_memory_bytes.{first,last,min,max,mean,count}` | `torch.cuda.memory_allocated()` at each step close | `_summary.sample_memory_at_step_close` (`_summary.py:632`) |
| `memory.peak_gpu_used_memory_bytes` | `memory.peak_gpu_used_memory_bytes.{first,last,…}` | `torch.cuda.max_memory_allocated()` at each step close | `_summary.py:634` |

### Collective GPU duration (summary CUDA sampling)

When L1 is OFF and `DD_PYTORCH_COLLECTIVE_GPU_SAMPLE_RATE > 0`, 1-in-
N collectives record CUDA events whose elapsed time feeds these
reservoirs. Each `<op>` is one of `allreduce`, `allgather`,
`broadcast`, `reducescatter`, `barrier`.

| Reservoir key | Facet prefix | Source site |
|---|---|---|
| `collective.<op>.gpu_duration_ms` | `collective.<op>.gpu_duration_ms.{count,min_ms,…,p99_ms}` | `_distributed.CudaEventResolver._drain_ready` (`_distributed.py:247`) |

### DDP gradient-comm bucket distributions

Emit only when the user has registered a custom DDP comm hook (see
`pytorch.grad_comm`). Independent of L1.

| Reservoir key | Facet prefix | Source site |
|---|---|---|
| `grad_comm.bucket_duration_ms` | `grad_comm.bucket_duration_ms.{count,min_ms,…,p99_ms}` | `_distributed._make_chained_comm_hook` (`_distributed.py:1149`) |
| `grad_comm.bytes_per_bucket` | `grad_comm.bytes_per_bucket.{count,min,max,mean,p10,p50,p90,p99}` (no `_ms`) | `_distributed.py:1151`. Present iff `bytes_count > 0`. |

### Model fingerprint facets (one-shot static values on `pytorch.rank`)

Set once at framework init by `_summary._fingerprint_model` and stamped
on the `pytorch.rank` span via `drain_all_to_facets()` at process exit.
These are NOT per-step reservoir values — they are module-level globals
that survive rotation drain cycles (i.e. `reset_all()` does not clear
them). All are conditional on `_model_fingerprinted = True`.

| Facet key | Type | Present iff | Notes |
|---|---|---|---|
| `model.param_count` | int (facet) | `_model_param_count > 0` | Total parameter count (`sum(p.numel() for p in model.parameters())`). |
| `model.trainable_param_count` | int (facet) | `_model_trainable_param_count > 0` | Count of parameters with `requires_grad=True`. |
| `model.is_transformer` | string (`"true"`/`"false"`) | always when fingerprinted | `"true"` when transformer-like blocks detected (via class-name or `self_attn`+`mlp` heuristic). |
| `model.dtype` | string | always when fingerprinted | `params[0].dtype` with `"torch."` prefix stripped (e.g. `"bfloat16"`, `"float32"`). |
| `model.layers` | int (facet) | `_model_layers > 0` (transformer only) | Count of transformer encoder/decoder blocks detected. |
| `model.hidden_dim` | int (facet) | `_model_hidden_dim > 0` (transformer only) | Modal `in_features` across all `nn.Linear` layers — proxy for embedding width. |
| `model.active_param_count` | int (facet) | `_model_active_param_count > 0 AND != _model_param_count` | MoE-adjusted active param count (top-K routing). Omitted for non-MoE models where active == total. |

**Source:** `_summary.drain_all_to_facets()` — emitted defensively; any
extraction failure is swallowed to prevent crashing the rank close.

### Counters

| Reservoir key | Facet | Source site |
|---|---|---|
| `train.amp_skipped_steps` | `train.amp_skipped_steps_count` (int) | `_hooks._drain_skipped_step_to_summary` (`_hooks.py:499`). Present iff at least one AMP-overflow-skipped step occurred. |

> **Reality check from the verification report (SHA `ad9ceab102`):**
> live samples on `pytorch.rank` observed all `step.{duration,forward,backward,optim_step}_ms.*`,
> `train.{loss,tflops}.*`, `optim.learning_rate.*`,
> `memory.{gpu_used_memory_bytes,peak_gpu_used_memory_bytes}.*`,
> `collective.allreduce.*`, `collective.ops_count`, `collective.p99_max_ms`.
> `step.data_fetch_ms.*` and `grad_comm.bucket_duration_ms.*` /
> `grad_comm.bytes_per_bucket.*` / `collective.grad_comm.*` were missing
> in that run; `step.data_fetch_ms` was a clock-mismatch bug since
> fixed at `26fe0ccae7`, and the `grad_comm` reservoirs are gated on a
> user comm hook that the verification load did not install.
> `step.grad_clip_ms.*` and `train.grad_norm.*` were correctly absent —
> the training loop did not call `clip_grad_norm_`.

---

## Layer-Zero DogStatsD metrics

Separate from spans: per-event distributions emitted from
`_metrics.record_collective`. **Always emitted at L0** regardless of
`collective_trace_enabled`. Namespace `pytorch.`.

| Metric | Type | Tags | Source |
|---|---|---|---|
| `pytorch.collective.duration_ms` | distribution | `device.id`, `host`, `kind`, `device.index`, `op` | `_metrics.record_collective` (`_metrics.py:69`) |
| `pytorch.collective.bytes` | distribution | same | `_metrics.py:70` |
| `pytorch.collective.calls_per_sec` | distribution | same | `_metrics.RateTicker._emit_tick` (`_metrics.py:250`) |
| `pytorch.collective.bytes_per_sec` | distribution | same | `_metrics.py:251` |

Tags are intentionally device-scoped (no `training_job.id`) to bound
cardinality at fleet size. Job-relative attribution is recovered via
the `pytorch.rank` span's time range.

Configuration:
- `DD_PYTORCH_RATE_TICKER_INTERVAL_S` (float, default `1.0`) — rate-ticker emission cadence. Setting `0.1` increases volume 10×.
- Failure backoff: after 10 consecutive `client.distribution` failures the ticker disables itself for 60 s.

---

## Ray

### Spans

| Operation | Tier | Default state |
|---|---|---|
| `ray.job` | Ray Core + dashboard-side | Emitted via `JobManager.submit_job` wrap; out-of-scope for this PR's value-add and documented for completeness. |
| `task.submit` / `task.execute` | Ray Core | `task.submit` requires `DD_TRACE_RAY_SUBMISSION_SPANS_ENABLED=true`; `task.execute` always emitted once Ray is patched. |
| `actor_method.submit` / `actor_method.execute` | Ray Core | Same gating. |
| `ray.get` / `ray.put` / `ray.wait` | Ray Core | Requires `DD_TRACE_RAY_CORE_API=true` (default false). |
| `ray.train.fit` | Ray Train | Requires Ray Core patched + `DD_TRACE_RAY_TRAIN_ENABLED=true` (default true) + `ray.train.torch.TorchTrainer` imported. |
| `ray.train.worker` | Ray Train | Same gating. |

Tier semantics:

| Tier | Trigger |
|---|---|
| Ray Core | `DD_PATCH_MODULES=ray:true` or `ddtrace.patch(ray=True)`. **Ray is NOT auto-patched** (`ddtrace/_monkey.py:121`). |
| Ray Train | Ray Core patched + `DD_TRACE_RAY_TRAIN_ENABLED=true` (default) + the user imports `ray.train.torch`. |
| `ray.job` | Emitted by `JobManager.submit_job` wrap when running on the dashboard side. Out-of-scope for this PR. |

> **Reality check from the verification report:** `ray.train.fit`,
> `ray.train.worker`, `actor_method.execute`, and `ray.task` had 0
> spans observed because the Ray module was not patched on that
> verification run (Ray is not auto-patched). `ray.job` was observed
> with 53 tag keys.

---

### `ray.train.fit`

**Tier:** Ray Train
**What it represents:** The driver-side `TorchTrainer.fit()` call.
Becomes the parent of one `ray.train.worker` span per rank (or those
spans become trace roots and link back to fit in per-rank-trace mode).
**Frameworks:** `ray.train.torch.TorchTrainer` (Ray ≥ 2.46.0).
**Overhead:** One span lifetime; driver-side. ~1 ms span open
including metadata RPC.
**Source:** `ddtrace/contrib/internal/ray/train.py:_wrapped_torch_trainer_fit`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `training_job.id` | string | Yes | Resolved by `__init__` wrap: `DD_PYTORCH_JOB_ID` → `_RAY_SUBMISSION_ID` env → `RuntimeContext.get_job_id()` → env chain (`RAY_JOB_ID` / `TORCHELASTIC_RUN_ID` / `KUBEFLOW_TRAINING_JOB_ID` / `SLURM_JOB_ID`) → UUID. |
| `ray.submission_id` | string | Yes | `_RAY_SUBMISSION_ID` env or fallback to `training_job_id`. |
| `training_job.framework` | string | Yes | Constant `"torch"`. |
| `training_job.trainer` | string | Yes | Constant `"TorchTrainer"`. |
| `training_job.status` | string | Yes | `"running"` at open; updated to `"succeeded"` on return or `"failed"` on exception. |
| `world_size` | string + int facet | Conditional | Set from `ScalingConfig.num_workers`. |
| `manual.keep` | tag | Yes | Force-keep. |
| `ray.train.run_name` | string | Conditional | From `RunConfig.name`. |
| `ray.metadata.<k>` | string | Conditional | Each k/v from `JobSubmissionClient.get_job_info(submission_id).metadata`. |
| `ray.train.num_workers` | int (facet) | Conditional | From `ScalingConfig.num_workers`. |
| `ray.train.use_gpu` | string (`"true"`/`"false"`) | Conditional | From `ScalingConfig.use_gpu`. |
| `ray.train.placement_strategy` | string | Conditional | From `ScalingConfig.placement_strategy`. |
| `ray.train.accelerator_type` | string | Conditional | From `ScalingConfig.accelerator_type`. |
| `ray.train.resources_per_worker.<k>` | float (facet) | Conditional | From `ScalingConfig.resources_per_worker` dict. |
| `ray.train.trainer_resources.<k>` | float (facet) | Conditional | From `ScalingConfig.trainer_resources` dict. |
| `ray.train.storage_path` | string | Conditional | From `RunConfig.storage_path`. |
| `ray.train.max_failures` | int (facet) | Conditional | From `RunConfig.failure_config.max_failures`. |
| `ray.train.checkpoint.num_to_keep` | int (facet) | Conditional | From `CheckpointConfig.num_to_keep`. |
| `ray.train.checkpoint.frequency` | int (facet) | Conditional | From `CheckpointConfig.checkpoint_frequency`. |
| `ray.train.checkpoint.score_attribute` | string | Conditional | From `CheckpointConfig.checkpoint_score_attribute`. |
| `ray.train.checkpoint.score_order` | string | Conditional | From `CheckpointConfig.checkpoint_score_order`. |
| `dd.trace_mode` | string | Conditional | `"per_rank"` when `DD_TRACE_RAY_TRAIN_PER_RANK_TRACE=true`. |
| `ray.hostname` / `ray.job_id` / `ray.node_id` / `ray.worker_id` / `ray.namespace` / `ray.gcs_address` / `ray.dashboard_url` / `ray.version` / `ray.task_id` / `ray.actor_id` | string | Conditional | From `_set_runtime_context_attributes`. |
| `error.type` / `error.message` / `error.stack` | string | On exception | Re-raised after tagging. |

`ray.train.fit` is pinned `USER_KEEP` to prevent head-based sampling
from dropping it while its USER_KEEP children survive.

---

### `ray.train.worker`

**Tier:** Ray Train
**What it represents:** One Ray Train worker actor running the user's
`train_loop_per_worker` (i.e., one rank). Either parented under
`ray.train.fit` or, when `DD_TRACE_RAY_TRAIN_PER_RANK_TRACE=true`, a
trace root with a span_link to fit.
**Frameworks:** `ray.train.torch.TorchTrainer`.
**Overhead:** One span lifetime per worker; ~1 ms span open.
**Source:** `ddtrace/contrib/internal/ray/train.py:_run_train_func_in_worker`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `training_job.id` | string | Yes | Inherited from driver (or from `_RAY_SUBMISSION_ID` env fallback when driver did not pre-resolve). |
| `ray.submission_id` | string | Yes | Worker's own `_RAY_SUBMISSION_ID` env or driver-supplied fallback. |
| `rank` | string | Conditional | From `ray.train.get_context().get_world_rank()` → session API → `RANK` env. Sets `span.resource = "rank=<rank>"`. |
| `world_size` | string | Conditional | From `ray.train.get_context().get_world_size()` → `WORLD_SIZE` env. |
| `ray.train.local_rank` | int (facet) | Conditional | `get_local_rank()` |
| `ray.train.node_rank` | int (facet) | Conditional | `get_node_rank()` |
| `ray.train.local_world_size` | int (facet) | Conditional | `get_local_world_size()` |
| `ray.train.run_name` | string | Conditional | Inherited from driver via wrapper. |
| `ray.metadata.<k>` | string | Conditional | Inherited from driver. |
| `ray.hostname` / `ray.job_id` / `ray.node_id` / `ray.worker_id` / `ray.namespace` / `ray.gcs_address` / `ray.dashboard_url` / `ray.version` / `ray.task_id` / `ray.actor_id` | string | Conditional | From `_set_runtime_context_attributes`. |
| `error.type` / `error.message` / `error.stack` | string | On exception | Re-raised after tagging. |

The worker span uses the long-running-span manager so it can survive
across span-rotation boundaries.

---

### `task.execute`

**Tier:** Ray Core
**What it represents:** One execution of a `@ray.remote` task on a
worker. Distributed context recovered from the `_dd_ray_trace_ctx`
kwarg (injected by `task.submit`) or, failing that, from env.
**Frameworks:** `ray.remote_function.RemoteFunction`.
**Overhead:** One span per task execution.
**Source:** `ddtrace/contrib/internal/ray/core/remote_function.py:_wrap_task_execution`; subscriber `ddtrace/_trace/subscribers/ray.py:RayExecutionSubscriber`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `ray.task.args` | string | Conditional | Present iff `DD_TRACE_RAY_ARGS_KWARGS=true`. Truncated/redacted. |
| `ray.task.kwargs` | string | Conditional | same |
| `ray.hostname` / `ray.job_id` / `ray.node_id` / `ray.worker_id` / `ray.namespace` / `ray.gcs_address` / `ray.dashboard_url` / `ray.version` / `ray.task_id` / `ray.actor_id` / `ray.submission_id` | string | Conditional | From `_set_runtime_context_attributes`. |
| `_dd.ai_observability.enabled` / `_dd.djm.enabled` / `_filter.kept` / `_dd.span.measured` / `_sampling_priority_v1` | int (facet) | Yes | From `_set_dist_ai_metrics`. |
| `error.type` / `error.message` / `error.stack` | string | On exception | |

---

### `task.submit`

**Tier:** Ray Core, gated on `DD_TRACE_RAY_SUBMISSION_SPANS_ENABLED=true` (default false)
**What it represents:** One `RemoteFunction._remote()` call — the
caller-side submission of a Ray task.
**Source:** `ddtrace/contrib/internal/ray/core/remote_function.py:traced_submit_task`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `ray.task.function_module` | string | Conditional | From `instance._function.__module__`. |
| `ray.task.function_qualname` | string | Conditional | From `__qualname__`. |
| `ray.task.num_cpus` | float (facet) | Conditional | From `_remote(num_cpus=…)`. |
| `ray.task.num_gpus` | float (facet) | Conditional | |
| `ray.task.num_returns` | int (facet) | Conditional | |
| `ray.task.max_retries` | int (facet) | Conditional | |
| `ray.task.accelerator_type` | string | Conditional | |
| `ray.task.scheduling_strategy` | string | Conditional | Type name (e.g. `NodeAffinitySchedulingStrategy`). |
| `ray.task.resources.<k>` | float (facet) | Conditional | Each k/v in `resources` dict. |
| `ray.task.args` / `ray.task.kwargs` | string | Conditional | `DD_TRACE_RAY_ARGS_KWARGS=true`. |
| `ray.task.submit_status` | string facet | Yes | `"success"` or `"error"`. |
| `ray.hostname` / `ray.job_id` / `ray.node_id` / `ray.worker_id` / `ray.namespace` / `ray.gcs_address` / `ray.dashboard_url` / `ray.version` / `ray.submission_id` | string | Conditional | |

---

### `actor_method.execute`

**Tier:** Ray Core
**What it represents:** One method call on a `@ray.remote` actor
class, executed on the actor worker. Method submissions inject
`_dd_ray_trace_ctx` so the execute span recovers distributed parent
context.
**Source:** `ddtrace/contrib/internal/ray/core/actor.py:_inject_tracing_actor_method` / `_inject_tracing_async_actor_method` / `_trace_actor_method_execution`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `ray.actor.class_name` | string | Conditional | From the actor instance class. |
| `ray.actor.module_name` | string | Conditional | |
| `ray.actor.method_name` | string | Conditional | |
| `ray.actor_method.args` / `ray.actor_method.kwargs` | string | Conditional | `DD_TRACE_RAY_ARGS_KWARGS=true`. |
| `ray.hostname` / `ray.job_id` / `ray.node_id` / `ray.worker_id` / `ray.namespace` / `ray.gcs_address` / `ray.dashboard_url` / `ray.version` / `ray.actor_id` / `ray.task_id` / `ray.submission_id` | string | Conditional | |
| `_dd.ai_observability.enabled` / `_dd.djm.enabled` / `_filter.kept` / `_dd.span.measured` / `_sampling_priority_v1` | int (facet) | Yes | |
| `error.type` / `error.message` / `error.stack` | string | On exception | |

Resource is set to `"<ClassName>.<method_name>"`.

Actor classes starting with `_` are not instrumented. Methods named
`ping` and `_polling` on `JobSupervisor` are intentionally excluded.
Users can additionally exclude classes/methods via
`DD_TRACE_RAY_IGNORED_ACTORS`.

---

### `actor_method.submit`

**Tier:** Ray Core, gated on `DD_TRACE_RAY_SUBMISSION_SPANS_ENABLED=true`.
**What it represents:** One `ActorHandle.<method>.remote()` call.
**Source:** `ddtrace/contrib/internal/ray/core/actor.py:traced_actor_method_submission`.

| Tag | Type | Always present? | Notes |
|---|---|---|---|
| `ray.actor.class_name` / `ray.actor.module_name` / `ray.actor.method_name` | string | Conditional | |
| `ray.actor_method.args` / `ray.actor_method.kwargs` | string | Conditional | `DD_TRACE_RAY_ARGS_KWARGS=true`. |
| `ray.actor_method.submit_status` | string facet | Yes | `"success"` or `"error"`. |
| `ray.hostname` / `ray.job_id` / `ray.node_id` / `ray.worker_id` / `ray.namespace` / `ray.gcs_address` / `ray.dashboard_url` / `ray.version` / `ray.submission_id` | string | Conditional | |

---

### `ray.get` / `ray.put` / `ray.wait`

**Tier:** Ray Core, gated on `DD_TRACE_RAY_CORE_API=true` (default false).
**Source:** `ddtrace/contrib/internal/ray/core/api.py:traced_get`, `traced_put`, `traced_wait`; subscriber `RayCoreAPITracingSubscriber`.

Operation names are exactly `ray.get`, `ray.put`, `ray.wait`.

Common tags (all spans):

| Tag | Type | Present iff |
|---|---|---|
| `ray.hostname` / `ray.job_id` / `ray.node_id` / `ray.worker_id` / `ray.namespace` / `ray.gcs_address` / `ray.dashboard_url` / `ray.version` / `ray.task_id` / `ray.actor_id` / `ray.submission_id` | string | Available from `_set_runtime_context_attributes`. |
| `_dd.ai_observability.enabled` / `_dd.djm.enabled` / `_filter.kept` / `_dd.span.measured` / `_sampling_priority_v1` | int (facet) | Yes |

Per-op:

| Op | Tag | Type | Notes |
|---|---|---|---|
| `ray.get` | `ray.get.value_size_bytes` | string | `sys.getsizeof(object_refs)` |
| `ray.get` | `ray.wait.timeout_s` | string | When `timeout=` is supplied. (Reuses the same tag constant as `ray.wait`.) |
| `ray.put` | `ray.put.value_type` | string | Python type name |
| `ray.put` | `ray.put.value_size_bytes` | string | `sys.getsizeof(value)` |
| `ray.wait` | `ray.wait.timeout_s` | string | |
| `ray.wait` | `ray.wait.num_returns` | string | |
| `ray.wait` | `ray.wait.fetch_local` | string | |

`ray.get` and `ray.wait` are registered as long-running spans (so
they survive span-rotation); `ray.put` is finished synchronously at
end of callback.

---

### `ray.job` (out of scope for this PR)

**Tier:** Dashboard-side, emitted by `JobManager.submit_job` wrap.
**Source:** `ddtrace/contrib/internal/ray/patch.py:traced_submit_job` + `ddtrace/_trace/subscribers/ray.py:RayJobStartSubscriber` + `ddtrace/contrib/internal/ray/span_manager.py` long-running-job manager.

Documented here for completeness only — `ray.job` predates this PR
and its schema is owned by the dashboard team. Observed tags from
the verification run:

| Tag | Type | Notes |
|---|---|---|
| `ray.entrypoint` | string | User entrypoint (paths redacted iff `DD_TRACE_RAY_REDACT_ENTRYPOINT_PATHS=true`, default true). |
| `ray.job.driver_agent_http_address` | string | From `JobInfo` post-completion. |
| `ray.job.driver_node_id` | string | |
| `ray.job.start_time_ms` / `ray.job.end_time_ms` | int (facet) | |
| `ray.job.has_runtime_env` | string (`"true"`/`"false"`) | |
| `ray.job.message` | string | From `JobInfo.message`. |
| `ray.job.metadata.<k>` | string | Each k/v from submission `--metadata-json`. |
| `ray.job.status` | string | `RUNNING`/`FINISHED`/`FAILED`. |
| `ray.job.submit_status` | string | `success`/`error`. |
| `ray.job_id` / `ray.submission_id` / `ray.hostname` / `ray.node_id` / `ray.worker_id` / `ray.namespace` / `ray.gcs_address` / `ray.version` | string | Runtime invariants. |
| `component` | string | `"ray"`. |
| `_dd.was_long_running` | int (facet) | `1` when the span was rotated through partials. |
| `_dd.partial_version` | int (facet) | Partial-span version counter. |

---

## Quick reference

| Trigger | What you get |
|---|---|
| `ddtrace.patch(pytorch=True)` + distributed signal | `pytorch.rank` per rank, summary-mode facets, L0 DogStatsD metrics |
| `+ DD_PYTORCH_COLLECTIVE_TRACE=true` | + `pytorch.allreduce`/`allgather`/`broadcast`/`reducescatter`/`barrier`/`allgather_into_tensor`/`reducescatter_tensor` spans, `pytorch.grad_comm` (if user installs DDP comm hook) |
| `+ DD_PYTORCH_PROFILING=true` | + `pytorch.step`/`pytorch.forward`/`pytorch.backward`/`pytorch.optimizer`/`pytorch.data_load` spans |
| `+ DD_PYTORCH_KERNEL_PROFILING=true` | + `pytorch.kernel` spans (requires L2) |
| `ddtrace.patch(ray=True)` | `task.execute`, `actor_method.execute`, `ray.job` (dashboard) |
| `+ DD_TRACE_RAY_SUBMISSION_SPANS_ENABLED=true` | + `task.submit`, `actor_method.submit` |
| `+ DD_TRACE_RAY_CORE_API=true` | + `ray.get`/`ray.put`/`ray.wait` |
| Ray patched + import `ray.train.torch` | + `ray.train.fit` (driver), `ray.train.worker` (per rank) |

---

## Configuration

### PyTorch env vars (source: `ddtrace/contrib/internal/pytorch/__init__.py`)

| Env var | Type | Default | Effect |
|---|---|---|---|
| `DD_PYTORCH_SERVICE` | string | unset | Override service name for pytorch spans. |
| `DD_PYTORCH_JOB_ID` | string | unset | Manual override of cross-rank job id. Highest priority in the chain. |
| `DD_PYTORCH_GRAD_COMM` | bool | `true` | Enable DDP comm-hook chaining. When false, user hooks run unwrapped. |
| `DD_PYTORCH_COLLECTIVE_TRACE` | bool | `false` | Enable Layer 1 per-collective spans. |
| `DD_PYTORCH_PROFILING` | bool | `false` | Enable Layer 2 step/forward/backward/optimizer/data_load spans. |
| `DD_PYTORCH_KERNEL_PROFILING` | bool | `false` | Enable Layer 3 `pytorch.kernel`. Requires `DD_PYTORCH_PROFILING=true`. |
| `DD_PYTORCH_RATE_TICKER_INTERVAL_S` | float | `1.0` | RateTicker cadence for `collective.calls_per_sec`/`bytes_per_sec`. |
| `DD_PYTORCH_FORCE_INSTALL` | bool | `false` | Install wrappers even without `RANK`/`WORLD_SIZE` env or prior `init_process_group`. |
| `DD_PYTORCH_SUMMARY_PROFILING` | bool | `true` | Enable summary-mode reservoirs + drain to `pytorch.rank` facets. |
| `DD_PYTORCH_CAPTURE_LOSS` | bool | `true` | Capture `loss.item()` via `Tensor.backward` wrap. Forces GPU→host sync per backward. |
| `DD_PYTORCH_MFU_ENABLED` | bool | `true` | Compute MFU + TFLOPS facets. Requires transformer-shaped model + matched peak-FLOPS lookup. |
| `DD_PYTORCH_COLLECTIVE_GPU_SAMPLE_RATE` | int | `100` | 1-in-N CUDA-event sampling for summary-mode collective GPU duration. `0` disables. |
| `DD_PYTORCH_STEP_OPTIMIZER` | string | unset | Class-name match for the designated optimizer that delimits step boundaries. |
| `DD_PYTORCH_PROFILE_WAIT_STEPS` | int ≥0 | `99` | torch.profiler schedule. |
| `DD_PYTORCH_PROFILE_WARMUP_STEPS` | int ≥1 | `1` | torch.profiler schedule. |
| `DD_PYTORCH_PROFILE_ACTIVE_STEPS` | int ≥1 | `5` | torch.profiler schedule. |
| `DD_PYTORCH_PROFILE_OFFSET_REFRESH_SEC` | int | `600` | Clock-offset refresh (currently no-op in production). |

Job-id env chain (read in order; first non-empty wins):
`DD_PYTORCH_JOB_ID` → `RAY_JOB_ID` → `TORCHELASTIC_RUN_ID` →
`KUBEFLOW_TRAINING_JOB_ID` → `SLURM_JOB_ID`. When none are set,
`training_job.id` is NOT stamped on spans and a one-time WARNING is
logged. There is no cross-rank broadcast.

Launcher env vars for `launcher` tag:
- `TORCHELASTIC_RUN_ID` → `torchrun`
- `_RAY_SUBMISSION_ID` or `RAY_JOB_ID` → `ray`
- `SLURM_JOB_ID` → `slurm`
- `KUBEFLOW_TRAINING_JOB_ID` → `kubeflow`

Install gate (`_should_install`): wrappers install iff
`DD_PYTORCH_FORCE_INSTALL=true` OR `RANK`/`WORLD_SIZE` env is set OR
`torch.distributed.is_initialized()` is true.

### Ray env vars (source: `ddtrace/contrib/internal/ray/__init__.py`, `patch.py`)

| Env var | Type | Default | Effect |
|---|---|---|---|
| `DD_RAY_SERVICE` | string | unset | Override service name for ray spans. |
| `DD_TRACE_RAY_CORE_API` | bool | `false` | Enable `ray.get`/`ray.put`/`ray.wait` spans. |
| `DD_TRACE_RAY_ARGS_KWARGS` | bool | `false` | Tag args/kwargs on task/actor spans. |
| `DD_TRACE_RAY_SUBMISSION_SPANS_ENABLED` | bool | `false` | Emit `task.submit` / `actor_method.submit` spans. |
| `DD_TRACE_RAY_IGNORED_ACTORS` | JSON | `{}` | `{ActorClass: ["method", ...] or "*"}` exclusion list. |
| `DD_TRACE_EXPERIMENTAL_LONG_RUNNING_FLUSH_INTERVAL` | float | `120.0` | Long-running span resubmit cadence. |
| `DD_TRACE_RAY_USE_ENTRYPOINT_AS_SERVICE_NAME` | bool | `false` | Use entrypoint script name as service when `DD_SERVICE` unset. |
| `DD_TRACE_RAY_REDACT_ENTRYPOINT_PATHS` | bool | `true` | Redact file paths in `ray.entrypoint` tag. |
| `DD_TRACE_RAY_TRAIN_ENABLED` | bool | `true` | Wrap `TorchTrainer.__init__` / `.fit`. |
| `DD_TRACE_RAY_TRAIN_PER_RANK_TRACE` | bool | `false` | Worker spans become trace roots with span_link to fit, instead of children. |

Worker env vars set by `JobManager.submit_job` wrap:
- `_RAY_SUBMISSION_ID` — submission id, used by every worker.
- `_RAY_JOB_NAME` — service name resolution.
- `DD_SERVICE` (setdefault) — keeps worker spans under the job's service.
- `DD_JOB_ENV_<NAME>` → `DD_<NAME>` propagation into the Ray runtime env (opt-in; the previous broad `DD_*` propagation leaked secrets).

---

## Expected overhead

| Tier | Per-op overhead | Notes |
|---|---|---|
| L0 metrics (always) | ~1 µs per collective (counter bump) + per-call DogStatsD send (~5 µs UDP). | RateTicker thread emits two distributions per op per `DD_PYTORCH_RATE_TICKER_INTERVAL_S` (default 1 s). |
| `pytorch.rank` | ~30 µs at open, bounded drain at close. | No periodic flush — hard-killed ranks lose the span. |
| Summary mode (per step) | ~10-20 µs: accumulator writes + 1-2 reservoir pushes per close. **Loss capture (`train.loss`) incurs one GPU→host sync per backward.** Disable via `DD_PYTORCH_CAPTURE_LOSS=false`. | Reservoir capped at 1024 samples. |
| Summary mode (collective GPU sampling) | 1-in-`DD_PYTORCH_COLLECTIVE_GPU_SAMPLE_RATE` collectives record CUDA events; ~5 µs per sample. Default 100 → ~0.02 % rate. | Set to `0` to disable. |
| L1 per-collective span | Span open + 2× CUDA event ops ~15 µs. | Background resolver thread services CUDA event polling; span finishes ~5 ms after the wall-clock end. |
| L1 `pytorch.grad_comm` (per bucket) | Span open + reservoir pushes ~25 µs. | Plus one `tracer.flush()` per backward (off the CUDA-callback thread). |
| L2 (per step) | ~30 µs combined for step + forward + backward + optimizer spans. Plus 1 GPU sync if `clip_grad_norm_` is used in the loop. | The summary-mode hooks already attached; L2 adds spans on top. |
| L3 (per kernel) | Bounded by torch.profiler schedule: `wait`/`warmup`/`active` defaults `99`/`1`/`5`. During active windows, kernel spans emit asynchronously on a CUPTI/Kineto thread. | One `tracer.flush()` per batch of kernels. |
| Ray actor_method.execute | One span lifetime per call. | Skips actors whose names start with `_` and the `JobSupervisor.ping` / `_polling` methods. |

---

## What's NOT collected (intentional)

These are out of scope at SHA `26fe0ccae7`. Frontend UX should not
assume any of the following are queryable:

1. **CUDA kernel-level data at L0/L1/L2.** Kernel attribution requires L3 (`DD_PYTORCH_KERNEL_PROFILING=true`).
2. **Gradient values or per-parameter statistics.** Only `train.grad_norm` (the scalar return of `clip_grad_norm_`) is captured, and only when the user calls that function.
3. **Model weights, activations, or per-layer feature maps.** No tensor contents are read.
4. **Dataset rows, sample identifiers, or input/output tensors.** `pytorch.data_load` carries only the data-fetch *duration*, no payload.
5. **Per-step or per-collective spans in default summary mode.** Summary mode rolls metrics into `pytorch.rank` facets — there is no per-step span until `DD_PYTORCH_PROFILING=true`.
6. **DDP default fused-allreduce traffic at L1.** DDP's native C++ fused-allreduce path bypasses Python. `pytorch.grad_comm` spans emit only when the user calls `model.register_comm_hook(state, hook)`. Layer-Zero metrics for grad_comm also require the user hook.
7. **NCCL communicator-level introspection** (which ranks share a communicator, ring topology, etc.). Only `torch.distributed.get_backend()` and NCCL env overrides are tagged.
8. **CPU-side `torch.profiler` events at L3.** Only CUDA kernel events become spans; CPU ops are skipped (`_profiler._is_kernel_event`).
9. **`SIGKILL` / `os._exit()` / segfaulted ranks.** No periodic flush; the rank span and its summary reservoirs are lost.
10. **Tokenizer state, optimizer hyperparameters beyond `lr`** (no betas / eps / weight_decay tags).
11. **DataLoader worker activity** (no instrumentation of `DataLoader` workers, only the time *between* steps).
12. **Distributed-checkpoint / saving I/O.** Not wrapped.
13. **Args / kwargs on PyTorch spans.** There is no `DD_TRACE_PYTORCH_ARGS_KWARGS`.
14. **Ray task/actor return values.** Sizes captured for `ray.get`/`ray.put` only (and only at `DD_TRACE_RAY_CORE_API=true`); contents never.
15. **Ray task/actor args/kwargs by default.** Opt-in via `DD_TRACE_RAY_ARGS_KWARGS=true`, truncated/redacted on tag.
16. **Cross-rank broadcast for job-id resolution.** Removed deliberately (caused NCCL crashes). When no env id is set, `training_job.id` is unset on every span.
