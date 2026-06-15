# PyTorch Instrumentation

Auto-instrumentation for PyTorch distributed training workloads. Bring training
into Datadog tracing with zero user code changes: a training job runs under
`ddtrace-run` (or `import ddtrace.auto`) and every rank automatically emits a
lightweight per-rank span + device-tagged metrics. Optional tiers add
per-collective spans, step profiling, and CUDA-kernel spans.

## Enabling

The PyTorch integration is **opt-in**. Enable explicitly via:

    DD_PATCH_MODULES=pytorch:true

or programmatically:

    import ddtrace
    ddtrace.patch(pytorch=True)

Even when enabled, `install()` is a no-op on processes that do not look
like distributed training. The integration installs its wrappers only
when ONE of the following is true:

- `RANK` or `WORLD_SIZE` environment variable is set.
- `torch.distributed.init_process_group()` has already been called.
- `DD_PYTORCH_FORCE_INSTALL=true` is set.

## Behavior changes

- **No default DDP comm-hook registration.** When the user does not
  install a custom comm hook (PowerSGD, fp16 compression, etc.), DDP
  uses its native fused C++ allreduce path and `pytorch.grad_comm`
  spans are not emitted. To enable grad-comm visibility, set
  `DD_PYTORCH_COLLECTIVE_TRACE=true` AND register a user comm hook;
  our wrapper chains it transparently.

- **No cross-rank job-id broadcast.** Set one of `DD_PYTORCH_JOB_ID`,
  `RAY_JOB_ID`, `TORCHELASTIC_RUN_ID`, `KUBEFLOW_TRAINING_JOB_ID`, or
  `SLURM_JOB_ID` to enable cross-rank trace correlation. When no id
  is found, each rank uses a local UUID internally but `training_job.id`
  is NOT emitted on spans (cross-rank correlation is degraded
  visibly rather than masked). A one-time WARNING is logged.

The integration ships in four tiers. The lowest tier is always-on; higher
tiers must be enabled explicitly.

| Tier | Flag | Overhead | What you get |
|------|------|----------|--------------|
| **Layer Zero** — fleet-stable metrics | always-on | ~0 % steady-state | One `pytorch.rank` span per rank for the job lifetime + DogStatsD distributions for collective duration/bytes/rate, all tagged by `device.id` (fleet-stable, not job-scoped) |
| **Layer One** — per-collective trace | `DD_PYTORCH_COLLECTIVE_TRACE=true` | ~1-2 % per step | Per-rank trace per collective, DDP gradient-comm timing, framework attribution. Use for straggler / NCCL debugging. |
| **Layer Two** — step profiling | `DD_PYTORCH_PROFILING=true` | ~0.3 ms per step | `pytorch.step` root with `pytorch.data_load` / `pytorch.forward` / `pytorch.backward` / `pytorch.optimizer` children |
| **Layer Three** — kernel profiling | `DD_PYTORCH_KERNEL_PROFILING=true` | ~5-15 % during active capture window only | Individual CUDA-kernel spans (`pytorch.kernel`) attributed to the step that issued them |

Supported PyTorch: 2.0–2.3. Supported Python: 3.9–3.12. Supported frameworks:
DDP, FSDP, DeepSpeed.

---

## Layer Zero — what gets emitted

**One span per rank, for the lifetime of the job:**

```
pytorch.rank
  service:  pytorch
  tags:     component=pytorch
            training_job.id=<resolved id>
            framework=ddp|fsdp|deepspeed|none
            device.id=GPU-abc-uuid     ← fleet-stable, links span ↔ metrics
            device.kind=cuda|cpu
            host=h-042
  metrics:  rank=7
            world_size=8000
            device.index=3
```

**Device-tagged DogStatsD distributions** (no `training_job.id` or `rank` on metrics — recover them from the span's time range):

| Metric | Cadence | Tags |
|---|---|---|
| `pytorch.collective.duration_ms` (distribution) | one sample per collective | `device.id`, `host`, `kind`, `device.index`, `op` |
| `pytorch.collective.bytes` (distribution) | one sample per collective | same |
| `pytorch.collective.calls_per_sec` (distribution) | 100 ms ticker → flushed 10 s | same |
| `pytorch.collective.bytes_per_sec` (distribution) | 100 ms ticker → flushed 10 s | same |

The per-event distributions preserve full spike resolution within the 10 s
flush via DDSketch local aggregation. The 100 ms ticker-derived rate
distributions catch sub-flush rate anomalies (e.g. a 200 ms bandwidth
collapse) at p99/max.

**Why device-tagged, not job-tagged.** A GPU UUID is a property of the
hardware: its NCCL behavior, thermal envelope, and tail latency persist
across workloads. Tagging metrics by `training_job.id` would create
`world_size × num_jobs` series; tagging by `device.id` caps cardinality at
the physical fleet size regardless of how many jobs run. Workload
attribution comes from the rank-root span's time range, not from the
metric tag.

---

## Configuration

All options are read from environment variables. Defaults are listed.

### Core

| Variable | Default | Description |
|----------|---------|-------------|
| `DD_PYTORCH_SERVICE` | `pytorch` | Service name reported on PyTorch spans. Falls back to `DD_SERVICE` if unset. |
| `DD_PYTORCH_JOB_ID` | _(see below)_ | Manual override for the cross-rank job identifier. When unset, the integration walks `RAY_JOB_ID` → `TORCHELASTIC_RUN_ID` → `KUBEFLOW_TRAINING_JOB_ID` → `SLURM_JOB_ID`. If no id is found, `training_job.id` is omitted from spans and a one-time WARNING is logged. Each rank resolves the id from env independently — no cross-rank broadcast. |
| `DD_PYTORCH_FORCE_INSTALL` | `false` | Set to `true` to install wrappers even when no distributed launcher signals are present. Useful for custom launchers. |

### Layer-0 tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `DD_PYTORCH_RATE_TICKER_INTERVAL_S` | `1.0` | Seconds between RateTicker emissions. Lower to `0.1` for debug-tier resolution at 10× the DogStatsD volume. |

### Layer-1 tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `DD_PYTORCH_GRAD_COMM` | `true` | Enable DDP gradient-communication tracing via comm hooks. Set `false` to skip comm-hook registration entirely. |

### Layer-2 tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `DD_PYTORCH_PROFILING` | `false` | Master switch for Layer 2. |
| `DD_PYTORCH_STEP_OPTIMIZER` | _(auto)_ | Optimizer class name that delimits step boundaries when multiple optimizers are used (e.g. `AdamW` in a GAN setup). When unset, the first optimizer instance to call `.step()` is auto-designated. |

### Layer-3 tuning

| Variable | Default | Description |
|----------|---------|-------------|
| `DD_PYTORCH_KERNEL_PROFILING` | `false` | Master switch for Layer 3. |
| `DD_PYTORCH_PROFILE_WAIT_STEPS` | `99` | Steps to wait before each capture window. |
| `DD_PYTORCH_PROFILE_WARMUP_STEPS` | `1` | Warm-up steps inside each window. |
| `DD_PYTORCH_PROFILE_ACTIVE_STEPS` | `5` | Active capture steps inside each window. With defaults: capture 5 steps every 105. |
| `DD_PYTORCH_PROFILE_OFFSET_REFRESH_SEC` | `600` | Interval between clock-offset recomputations (used to align profiler μs timestamps with wall-clock ns). |

---

## Span coverage

Every span carries `service=pytorch`, `component=pytorch`, and a `job_id` tag
(inherited from the parent trace context for kernel spans).

### Layer 1 — distributed (always-on)

| Span name | Source | Tags | Metrics |
|-----------|--------|------|---------|
| `pytorch.allreduce` | `torch.distributed.all_reduce` | `framework`, `job_id` | `bytes`, `rank`, `world_size`, `gpu.duration_ms`¹ |
| `pytorch.allgather` | `torch.distributed.all_gather` | same | same |
| `pytorch.broadcast` | `torch.distributed.broadcast` | same | same |
| `pytorch.reducescatter` | `torch.distributed.reduce_scatter` | same | same |
| `pytorch.barrier` | `torch.distributed.barrier` | same | `rank`, `world_size`, `gpu.duration_ms`¹ (no `bytes`) |
| `pytorch.allgather_into_tensor` | FSDP functional variant | same | same |
| `pytorch.reducescatter_tensor` | FSDP functional variant | same | same |
| `pytorch.grad_comm` | DDP bucket all-reduce via comm hook | `framework=ddp`, `job_id` | `bytes`, `rank` |

¹ `gpu.duration_ms` is recorded only when CUDA is available, the tensor is on a
CUDA device, and the process-group backend is not `gloo` or `mpi`. Otherwise
the span carries wall-clock duration only.

### Layer 2 — step (`DD_PYTORCH_PROFILING=true`)

| Span name | When opened | When closed | Tags | Metrics |
|-----------|-------------|-------------|------|---------|
| `pytorch.step` | First `forward_pre_hook` (or first non-AMP `optimizer.step`) of an iteration | End of designated `optimizer.step` | `component`, `skipped` (when AMP overflow) | `step` (counter) |
| `pytorch.data_load` | First forward of an iteration when a previous step has ended | Same call (synchronous emit) | `component` | — |
| `pytorch.forward` | `forward_pre_hook` | `forward_hook` | `component` | — |
| `pytorch.backward` | `Tensor.backward` wrap (`_wrapped_tensor_backward`) | end of `Tensor.backward()` | `component` | — |
| `pytorch.optimizer` | Instance-wrapped `optimizer.step` | end of `optimizer.step` | `component`, `optimizer=<class name>` | — |

Special cases:
- **Gradient accumulation:** N forward/backward cycles followed by one
  `optimizer.step` → one `pytorch.step` containing N forward + N backward + 1
  optimizer children.
- **AMP overflow (skipped step):** the designated `optimizer.step` is skipped by
  `GradScaler` → `pytorch.step[skipped=true]` is emitted without a
  `pytorch.optimizer` child, and the step counter does not increment.
- **LBFGS closure:** `optimizer.step(closure)` calls the closure N times → one
  `pytorch.step` with N forward + N backward children under one
  `pytorch.optimizer`.

### Layer 3 — kernel (`DD_PYTORCH_KERNEL_PROFILING=true`)

| Span name | Source | Tags | Metrics |
|-----------|--------|------|---------|
| `pytorch.kernel` | `torch.profiler` event during an active capture window | `kernel.name`, `stream_id` (and inherited `job_id`) | `duration_ms`, `flops` (when reported), `step`, `rank` |

Kernels are emitted asynchronously on the profiler's worker thread and are
correlated back to the `pytorch.step` span that issued them via a thread-safe
ring buffer keyed by `(step, rank)`. Kernel events that don't match any
recorded step window are dropped, with a sampled warning every 50 misses.

---

## Lifecycle

- **Module import:** `patch.py` runs at import time. It calls `config._add` and
  registers an idempotent `torch._datadog_patch` flag. No `torch.distributed`
  calls are made.
- **First `torch.distributed.init_process_group` call:** triggers the
  bootstrap — resolves the `job_id`, captures `rank` / `world_size`, starts
  the CUDA Event resolver thread. Bootstrap is one-shot per `patch()` cycle.
- **Late patching:** if `patch()` is called *after*
  `init_process_group` already ran (manual / delayed imports), the bootstrap
  runs immediately to avoid spans with empty rank metadata.
- **Layer 2 attachment:** Layer 2 hooks are registered on the inner user
  model (`.module` for DDP/FSDP/DeepSpeed wrappers) at framework
  `__init__` time when `DD_PYTORCH_PROFILING=true`. Handles are tracked in a
  `WeakKeyDictionary` so `unpatch()` cleanly removes them.
- **Layer 3 attachment:** the `torch.profiler` instance is built lazily on the
  first designated `optimizer.step` after a `pytorch.step` closes when
  `DD_PYTORCH_KERNEL_PROFILING=true`. The schedule advances only on designated
  optimizer steps (so AMP-skipped iterations don't burn capture cycles).
- **`unpatch`:** unwraps every patched call site, joins the CUDA Event resolver
  thread with a 2-second deadline (any still-pending spans are finished with
  `_dd.error_reason="cuda_event_unresolved"`), detaches Layer 2 hook handles,
  and stops the Layer 3 profiler if it was running.

---

## Example: single 2-rank DDP training job

Setup:

```python
# torchrun --nproc_per_node=2 train.py
import torch, torch.distributed as dist
import torch.nn as nn
from torch.nn.parallel import DistributedDataParallel as DDP

dist.init_process_group(backend="nccl")
model = DDP(nn.Linear(64, 64).cuda())
optimizer = torch.optim.AdamW(model.parameters(), lr=1e-3)
scaler = torch.cuda.amp.GradScaler()

for x, y in dataloader:                  # ─── iteration N ───
    optimizer.zero_grad()
    with torch.cuda.amp.autocast():
        out = model(x.cuda())
        loss = nn.functional.mse_loss(out, y.cuda())
    scaler.scale(loss).backward()
    scaler.step(optimizer)
    scaler.update()
```

Run as:

```
DD_PYTORCH_PROFILING=true \
DD_PYTORCH_KERNEL_PROFILING=true \
DD_PYTORCH_STEP_OPTIMIZER=AdamW \
ddtrace-run torchrun --nproc_per_node=2 train.py
```

### Spans emitted on rank 0 for iteration N = 101

This iteration is inside a Layer-3 active capture window (steps 100–104 with
default `wait=99/warmup=1/active=5`), so kernel spans are emitted.

```
pytorch.step  ─────────────────────────────────────────────  [root]
  service:    pytorch
  tags:       component=pytorch
              job_id=ab12cd34-...
              optimizer=AdamW
  metrics:    step=101
              rank=0
              world_size=2
  duration:   ~38ms
  │
  ├── pytorch.data_load
  │     tags:    component=pytorch
  │     start:   end of step 100's optimizer.step
  │     end:    first forward_pre_hook of iteration 101
  │     duration: ~2ms (DataLoader gap)
  │
  ├── pytorch.forward
  │     tags:    component=pytorch
  │     duration: ~12ms
  │     │
  │     ├── pytorch.kernel
  │     │     tags:    kernel.name=ampere_sgemm_128x64
  │     │              stream_id=7
  │     │     metrics: duration_ms=2.8
  │     │              flops=2.4e10
  │     │              step=101
  │     │              rank=0
  │     │
  │     └── pytorch.kernel
  │           tags:    kernel.name=elementwise_kernel
  │                    stream_id=7
  │           metrics: duration_ms=0.3
  │                    step=101
  │                    rank=0
  │
  ├── pytorch.backward
  │     tags:    component=pytorch
  │     duration: ~22ms
  │     │
  │     ├── pytorch.kernel
  │     │     tags:    kernel.name=ampere_sgemm_128x64_bw
  │     │     metrics: duration_ms=5.7, flops=4.8e10, step=101, rank=0
  │     │
  │     └── pytorch.grad_comm  (DDP bucket all-reduce, fires inside backward)
  │           tags:    framework=ddp, job_id=ab12cd34-...
  │           metrics: bytes=48234496, rank=0, gpu.duration_ms=8.2
  │           │
  │           └── pytorch.kernel
  │                 tags:    kernel.name=ncclAllReduce
  │                          stream_id=15
  │                 metrics: duration_ms=8.0, step=101, rank=0
  │
  └── pytorch.optimizer
        tags:    component=pytorch
                 optimizer=AdamW
        duration: ~2ms
        │
        └── pytorch.kernel
              tags:    kernel.name=adamw_kernel
              metrics: duration_ms=1.4, step=101, rank=0
```

### What the same iteration looks like on rank 1

Rank 1 emits an independent root trace with the same shape. Cross-rank
correlation is by `training_job.id`, which is identical on every rank because
every rank resolves it from the same env source (`DD_PYTORCH_JOB_ID`,
`RAY_JOB_ID`, `TORCHELASTIC_RUN_ID`, `KUBEFLOW_TRAINING_JOB_ID`, or
`SLURM_JOB_ID`). When none of those env vars are set, the tag is omitted on
every rank — operators must set one to enable cross-rank correlation.
Per-rank metrics like `gpu.duration_ms` on `pytorch.grad_comm` reveal stragglers.

### What changes outside the active window (e.g. iteration N = 50)

Identical span tree minus all the `pytorch.kernel` children. Layer 3 only
contributes spans during the 5 active steps per 105-step cycle.

### What happens if AMP overflows on iteration N = 102

```
pytorch.step
  tags:    component=pytorch
           skipped=true                    ← step was skipped
           job_id=ab12cd34-...
  metrics: rank=0
           world_size=2
                                            (no `step` metric increment;
                                             counter stays at 101)
  ├── pytorch.data_load
  ├── pytorch.forward
  └── pytorch.backward
                                            (no `pytorch.optimizer` child;
                                             GradScaler bypassed the inner step)
```

---

## Operational notes

- **Backends without CUDA timing:** on `gloo` or `mpi`, collective spans omit
  `gpu.duration_ms` and report wall-clock duration only.
- **CPU-only PyTorch builds:** the integration short-circuits the collective
  wrappers when `torch.distributed.is_available()` is `False`. `patch()` /
  `unpatch()` round-trip cleanly with no wrapping.
- **DDP comm hook on PyTorch 2.0:** `_pre_backward_hook` was added in 2.1.
  On 2.0 the lazy comm-hook fallback is disabled and DDP gradient communication
  is traced via `torch.distributed.all_reduce` only (bucket-level granularity
  is not available).
- **DeepSpeed coexistence:** wrappers are installed when `deepspeed` is
  importable. Wrapper identity is preserved through DeepSpeed's own
  monkey-patching so both layers fire.
- **Cross-rank `job_id` correlation:** requires an env-supplied id
  (`DD_PYTORCH_JOB_ID`, `RAY_JOB_ID`, `TORCHELASTIC_RUN_ID`,
  `KUBEFLOW_TRAINING_JOB_ID`, or `SLURM_JOB_ID`). When none is present,
  `training_job.id` is omitted from spans and a one-time WARNING is logged;
  cross-rank correlation is degraded visibly rather than masked.

## Ray Train composition

When the PyTorch contrib is loaded *and* the Ray contrib is loaded, a single
`ray.train.torch.TorchTrainer.fit()` call produces a trace shaped as:

```
ray.train.fit
  ├── ray.train.worker [rank=0]
  │     ├── pytorch.step
  │     │     ├── pytorch.forward
  │     │     │     └── pytorch.kernel (× N)
  │     │     ├── pytorch.backward
  │     │     │     └── pytorch.grad_comm
  │     │     └── pytorch.optimizer
  │     └── pytorch.allreduce (× M)
  └── ray.train.worker [rank=1] … [rank=N-1]
```

Every span on every rank carries the canonical `training_job.id` tag,
resolved from the env chain `DD_PYTORCH_JOB_ID → RAY_JOB_ID →
TORCHELASTIC_RUN_ID → KUBEFLOW_TRAINING_JOB_ID → SLURM_JOB_ID` and
propagated to every worker via the picklable wrapper. When no id is found,
`training_job.id` is omitted and a one-time WARNING is logged.

### Configuration

| Variable | Default | Effect |
|---|---|---|
| `DD_TRACE_RAY_TRAIN_ENABLED` | `true` | Master switch for `ray.train.fit` / `ray.train.worker` emission. |
| `DD_TRACE_RAY_TRAIN_PER_RANK_TRACE` | `false` | When `true`, each `ray.train.worker` opens as a new trace root with a `span_link` back to `ray.train.fit`. Use for very large clusters where the per-rank × per-step span count would exceed Datadog's per-trace limit. |
| `DD_PYTORCH_JOB_ID` | _(unset)_ | User-supplied training job id; overrides the env-chain resolution. Must be unique per run — back-to-back runs with the same id collide in the long-running-span manager. |

---

## Open issues / follow-up work

> **Superseded** by Layer Zero (default-on device-tagged metrics + per-rank lifetime span). The designated-rank-sampling proposal below remains valid as a *Layer One debugging knob* — i.e. when an operator opts into per-collective spans for straggler investigation, designation reduces the per-trace span count. It is no longer needed for default-install scalability.

### Layer 1 designated-rank sampling

**Problem.** Layer 1 wraps `torch.distributed.{all_reduce, broadcast, reduce_scatter, all_gather, barrier, ...}` and emits one span per collective call on *every* rank that participates. For a kilo-rank, multi-day training run, the resulting span volume blows past Datadog's per-trace cap and inflates ingest billing even after agent-side adaptive sampling kicks in:

- 8-GPU host × 1000 hosts × ~25 collective spans/sec/rank ≈ 200k spans/sec cluster-wide.
- Over a week that is ≈ 15 billion spans, ~150 MB/s sustained network on the trace-agent → backend hop (compressed), and a sustained encode/ship CPU cost on the train-loop critical path of every rank.
- The `DD_TRACE_RAY_TRAIN_PER_RANK_TRACE` per-rank-trace fallback only thins per-trace span counts by `world_size`×; it does not change the absolute emission rate or the cluster aggregate.
- `DD_TRACE_SAMPLE_RATE` is the wrong knob: head-based trace sampling is all-or-nothing per trace, so at e.g. 0.05 it silently drops 95% of jobs entirely instead of thinning each job's volume. It also doesn't avoid the in-process span-construction cost (the sampling decision is made *after* the span is built).

**Why naive in-tracer sampling is also wrong.** A per-collective random drop (e.g. emit 1-in-N spans on every rank) breaks the rank-symmetry invariant: collectives are barrier-synchronous across ranks, so if rank 7 keeps a span for `all_reduce` #1234 and rank 3 drops it, the resulting trace tree has gaps that are indistinguishable from a stuck rank — false signal for the operator.

**Proposal.** Adopt the Layer 2 designation pattern for Layer 1: by default emit Layer 1 spans only from one designated rank per process group (or per node), where designation is deterministic (lowest rank in the local communicator, the same rank that already gets Layer 2 / Layer 3 spans). Non-designated ranks short-circuit the collective wrapper after recording lightweight counters but before constructing the ddtrace `Span`. Collective timings are symmetric across ranks, so the designated rank's record is operationally representative; the other ranks contribute zero span-construction cost on the train loop's critical path and zero ingest volume.

**Config surface.**

| Variable | Default | Effect |
|---|---|---|
| `DD_TRACE_PYTORCH_LAYER1_DESIGNATED_RANK_ONLY` | `true` | When `true`, only the designated rank in each process group emits Layer 1 collective spans. Other ranks short-circuit before span construction. Set `false` to restore today's emit-on-every-rank behavior (useful for debugging straggler/divergence issues where per-rank collective timings matter). |
| `DD_TRACE_PYTORCH_LAYER1_DESIGNATED_RANK` | _(unset)_ | Override the designated rank explicitly (default: derived from the same designation logic Layer 2 uses, typically `rank == 0` per process group). |

`DD_TRACE_SAMPLE_RATE` remains the documented operator escape hatch for very large jobs that want to thin even the designated rank's emission rate.

**Rollout.**

1. Land the rank-designation gate in `_distributed.py`'s collective wrappers behind `DD_TRACE_PYTORCH_LAYER1_DESIGNATED_RANK_ONLY` defaulted to `false`. Existing behavior unchanged.
2. Add unit tests covering both modes and the per-process-group selection.
3. Flip the default to `true` in a follow-up release after a deprecation note; document the off-by-default knob for the straggler-debugging use case.

**Out of scope for the initial Ray↔PyTorch integration.** Filing here as a follow-up issue. The same designation logic could later be extended to Layer 2 / Layer 3 for symmetry; today Layer 2 already designates one optimizer per process for full step tracing, so most of the plumbing already exists.

#### Straggler detection trade-off

Designating a single rank loses the per-rank comparison signal that straggler detection relies on: spans from a slow rank no longer appear, and the designated rank's `pytorch.allreduce.gpu.duration_ms` already reflects the slowest peer (collectives are barrier-synchronous, so the local elapsed time on every rank is dominated by whoever arrived last). You can detect that *a* straggler exists in the cluster from the designated rank's tail latency, but not *which* rank is the offender.

Two recovery paths the v1 should ship with:

1. **Operator escape hatch.** `DD_TRACE_PYTORCH_LAYER1_DESIGNATED_RANK_ONLY=false` reverts to emit-on-every-rank for the duration of a debugging window. Spans then carry the `rank` tag and the operator can compare timings across ranks in APM. Slow feedback loop but zero implementation cost.
2. **Always-on per-rank metrics.** Independent of span sampling, emit a lightweight DogStatsD gauge — e.g. `pytorch.collective.duration_ms` tagged with `rank`, `world_size`, `op` (allreduce/broadcast/…), `training_job.id` — on *every* rank, regardless of designation. Metrics aggregate cheaply on the backend (no per-trace caps, much smaller wire format) and the standard p50/p99 per-rank diff chart drops out for free. This is the standard pattern at scale (NVIDIA Megatron's collective profiler, AWS SageMaker's training-job dashboards).

Recommendation for v1: ship designation + escape hatch; file the per-rank-metrics path as the next follow-up after this one.

#### Adaptive designation (warm-up window)

A flat "always designate" default surprises short-lived jobs that would otherwise see every collective in their trace — CI smoke runs, single-epoch fine-tunes, debugging sessions. An adaptive policy gives small jobs full fidelity while protecting long jobs from the volume explosion.

**Design.** Each rank starts in *full-emission* mode. After `WARMUP_SECONDS` since its first collective *or* `WARMUP_COLLECTIVES` count reached (whichever trips first), the rank atomically switches to designated-rank-only emission for the remainder of the process. Inter-rank NTP skew is ~ms while a training step is 50–500 ms, so all ranks transition within a fraction of a step — no mixed-sampling drift in practice.

This also catches the universally-interesting "first iteration is slow" phase (CUDA init, kernel JIT, NCCL warm-up) at full fidelity without any operator config.

| Variable | Default | Effect |
|---|---|---|
| `DD_TRACE_PYTORCH_LAYER1_FULL_WARMUP_SECONDS` | `300` | After this many seconds since the first collective on a rank, switch from emit-on-every-rank to designated-rank-only. `0` disables the warm-up (immediate designation, behaves like today's proposal). `-1` disables designation entirely (always full emission — useful for straggler-debugging runs). |
| `DD_TRACE_PYTORCH_LAYER1_FULL_WARMUP_COLLECTIVES` | `2000` | Companion `OR` threshold — whichever trips first. Protects very-low-rate workloads (small models, slow data) that take longer than `WARMUP_SECONDS` to reach their first interesting step. |

Implementation note: the per-rank counters and the elapsed-time check live in the collective wrapper hot path, so they must be cheap — a single monotonic timestamp comparison + atomic increment, no thread-locks.

#### Remote-config-driven cutover (v2)

The adaptive policy above is local: each rank decides based on its own clock and counter. A more correct design at billing scale is to back the cutover off **Datadog's remote-config channel**, which the integration already subscribes to (visible in the verify-run debug logs as `APM_TRACING` payloads). The backend observes the per-customer span volume and pushes a "switch to designated-rank-only" flag when the rate crosses a per-customer budget threshold. Benefits:

- Threshold tracks the customer's actual cost / quota rather than a hard-coded local heuristic.
- Operators can override per-job from the Datadog UI without restarting workers.
- The signal is consistent across the cluster (one config push reaches every rank's tracer at roughly the same wall-clock time, modulo poll interval).

Defer to v2 because it requires backend-side product work (the budget-tracking + push-config piece) that isn't part of the dd-trace-py contrib.
