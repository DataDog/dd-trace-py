# PyTorch L0 rank-span extraction — design spec

**Date:** 2026-06-03
**Branch:** `kr-igor/pytorch-rank-span` (off `main`)
**Parent branch:** `kr-igor/pytorch-experiments`

## Goal

Extract the always-on `pytorch.rank` lifetime span into a standalone,
reviewable PR that has no new user-facing configuration knobs and no
dependency on the summary, profiling, or per-collective-span machinery.

This is a subset of the P1 slice described in `split-pr-instructions.md`.
The collective wrappers, `_metrics.py`, `_summary.py`, `_hooks.py`, and
`_profiler.py` are all excluded; they land in subsequent PRs.

## Scope

### What's in

- `pytorch.rank` span lifecycle: open at `init_process_group`, close at
  `destroy_process_group` / atexit.
- Span tags: `rank`, `world_size`, `framework` (DDP / FSDP / DeepSpeed),
  `launcher`, `torch.distributed.backend`, `training_job_id`.
- Ray Train context tagging: `ray.train.run_name`, `ray.submission_id`,
  `ray.metadata.*` — stamped via `retag_ray_run_context()` (called by Ray
  Train after populating the run-metadata cache) and re-applied at close as
  a safety net.
- Run-metadata cache (`set_cached_run_metadata` / `get_cached_run_metadata`
  / etc.) in `_utils.py` — written by Ray Train, read by `_rank_root.py`.
- Job ID auto-resolution: walks
  `RAY_JOB_ID → TORCHELASTIC_RUN_ID → KUBEFLOW_TRAINING_JOB_ID →
  SLURM_JOB_ID → UUID4`; no manual override in this PR.

### What's out

- Collective monkey-patch wrappers (`_install_collectives`,
  `_make_collective_wrapper`, `_make_chained_comm_hook`).
- `_metrics.py` — duration reservoirs and collective facets.
- `_summary.py` — training-step metrics, MFU, model fingerprint.
- `_hooks.py` — step/forward/backward/optimizer instrumentation.
- `_profiler.py` — CUDA kernel profiling.
- `CudaEventResolver` and all summary-mode CUDA sampling.
- All new env-var config knobs (`DD_PYTORCH_*`). The only activation
  mechanism is `DD_PATCH_MODULES=pytorch:true` (existing umbrella).

## Approach

File-level extraction: for each file, `git restore --source
kr-igor/pytorch-experiments -- <file>`, then surgically delete non-L0
sections. Start from tested code; the stripping is mechanical.

## File list

| File | Action | Key strips |
|------|--------|------------|
| `ddtrace/contrib/internal/pytorch/__init__.py` | pull + strip | Remove `DD_PYTORCH_COLLECTIVE_TRACE`, `DD_PYTORCH_PROFILING`, `DD_PYTORCH_SUMMARY_PROFILING`, `DD_PYTORCH_CAPTURE_LOSS`, `DD_PYTORCH_MFU_ENABLED`, `DD_PYTORCH_COLLECTIVE_GPU_SAMPLE_RATE`, `DD_PYTORCH_KERNEL_PROFILING`, `DD_PYTORCH_JOB_ID`, `DD_PYTORCH_FORCE_INSTALL` env-var sections and matching `config.*` keys |
| `ddtrace/contrib/internal/pytorch/_rank_root.py` | pull + strip | Remove `_tag_collective_summary` function entirely (both `_metrics` and `_summary` are excluded); remove its call from `close()`; remove `_metrics` import. Keep full span lifecycle, `_tag_ray_run_context`, `retag_ray_run_context` |
| `ddtrace/contrib/internal/pytorch/_distributed.py` | pull + gut (~90%) | Keep only: `_bootstrap_lock`, `_bootstrap_distributed`, `_wrapped_init_process_group`, `_wrapped_destroy_process_group`, `_detect_launcher`, `_get_cached_backend`, DDP/FSDP/DeepSpeed `set_framework` stubs, `install`/`uninstall` (patching `init_process_group` + `destroy_process_group` + framework-detection hooks only). Drop `_should_install()` — install is unconditional when `DD_PATCH_MODULES=pytorch:true`. Drop all collective wrappers, hooks, CUDA event resolver. |
| `ddtrace/contrib/internal/pytorch/_device.py` | pull as-is | Nothing — used by `_build_span` for device tags |
| `ddtrace/contrib/internal/pytorch/_utils.py` | pull + strip | Keep: `set_cached_run_metadata`, `get_cached_run_metadata`, `restore_run_metadata_snapshot`, `clear_cached_run_metadata`, `set_cached_job_id`, `get_cached_job_id`, `resolve_job_id_from_env`, `job_id_env_set`, `set_training_job_id_tag`, fork-safe `_reset_child_state`. Drop: collective/summary helpers, `_LAST_OPTIMIZER_STEP_END_NS`, AMP-skip helpers |
| `ddtrace/contrib/internal/pytorch/patch.py` | pull + strip | Remove collective/summary/profiling install calls; keep only `install()`/`uninstall()` dispatch to `_distributed.install`/`uninstall` |
| `ddtrace/contrib/internal/pytorch/_test_helpers.py` | pull as-is | Nothing |

## Data flow

```
torch.distributed.init_process_group()
  └─ _wrapped_init_process_group
       └─ _bootstrap_distributed()
            ├─ detect rank, world_size, backend
            ├─ resolve training_job_id (auto from env / UUID)
            └─ _rank_root.open(rank, world_size, framework, job_id)
                 └─ opens pytorch.rank span
                      └─ _build_span() stamps rank, world_size, framework,
                                               launcher, backend, training_job_id,
                                               ray.train.run_name (if cache set)

[Ray Train only — fires after init_process_group]
ray.train._run_train_func_in_worker()
  └─ populates run-metadata cache (run_name, submission_id, metadata)
  └─ _rank_root.retag_ray_run_context()
       └─ reads cache → stamps ray.train.run_name, ray.submission_id,
                                 ray.metadata.* on live pytorch.rank span

torch.distributed.destroy_process_group()  (or atexit)
  └─ _wrapped_destroy_process_group
       └─ _rank_root.close()
            ├─ _tag_ray_run_context(span)   ← safety-net re-stamp
            └─ span.finish() + background flush
```

## Env vars

No new env vars. Activation: `DD_PATCH_MODULES=pytorch:true` (existing).
Job ID is auto-resolved from launcher env; falls back to UUID4 per rank.

## Span emitted

`pytorch.rank` — always-on, one per rank lifetime.

Tags: `rank`, `world_size`, `framework`, `launcher`,
`torch.distributed.backend`, `training_job_id`,
`ray.train.run_name` (Ray only), `ray.submission_id` (Ray only),
`ray.metadata.*` (Ray only).

No facets in this PR (collective timing facets land with L1).

## Tests

| File | Scope |
|------|-------|
| `tests/contrib/pytorch/test_rank_root.py` | Full |
| `tests/contrib/pytorch/test_pytorch_patch.py` | Full |
| `tests/contrib/pytorch/test_pytorch.py` | Subset — rank span emission, job_id, rank/world_size tags |
| `tests/contrib/pytorch/test_fork_safety.py` | Subset — `_rank_root` globals reset |
| `tests/contrib/pytorch/test_repatch_and_exception_paths.py` | Subset — init/destroy wrapper paths |

## Release note

```
feat(pytorch): add always-on pytorch.rank lifetime span for distributed training

Instruments torch.distributed.init_process_group / destroy_process_group to
emit a single pytorch.rank span per rank. Tags: rank, world_size, framework
(DDP/FSDP/DeepSpeed), launcher, torch.distributed.backend, training_job_id
(auto-resolved from RAY_JOB_ID / TORCHELASTIC_RUN_ID / SLURM_JOB_ID / UUID),
and Ray Train run context (ray.train.run_name, ray.submission_id,
ray.metadata.*) when running under Ray Train.

No new configuration knobs. Enable with DD_PATCH_MODULES=pytorch:true.
```

## Not in scope

- `DD_PYTORCH_JOB_ID` manual override — auto-detection covers all supported launchers.
- `DD_PYTORCH_FORCE_INSTALL` — unconditional install when module is patched.
- Collective timing facets (`collective.<op>.*`) — land with L1 in the next PR.
- Summary-mode training metrics — land with L2+.
