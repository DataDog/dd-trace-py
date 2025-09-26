from __future__ import annotations

import os

import vllm
from vllm.engine.arg_utils import AsyncEngineArgs


def _create_llm_autotune(model, **kwargs):
    # Prefer smaller GPU budgets first to reduce OOM risk on CI
    util_candidates = kwargs.pop(
        "gpu_util_candidates",
        [0.05, 0.1, 0.15, 0.2, 0.3, 0.5, 0.7, 0.85, 0.9],
    )
    # On CI, set a conservative SchedulerConfig (v1) if available
    if os.environ.get("CI") == "true" and "scheduler_config" not in kwargs:
        try:
            from vllm.v1.config import SchedulerConfig  # type: ignore

            kwargs["scheduler_config"] = SchedulerConfig(
                max_num_batched_tokens=256,
                max_num_seqs=1,
            )
        except Exception:
            pass
    last_error = None
    for util in util_candidates:
        try:
            return vllm.LLM(model=model, gpu_memory_utilization=util, **kwargs)
        except Exception as exc:
            last_error = exc
            continue
    raise last_error


def get_llm(model: str, *, engine_mode: str = "0", **kwargs):
    # Avoid passing runner=None to vLLM; default runner is 'generate'
    runner = kwargs.get("runner")
    should_diag = os.environ.get("DD_VLLM_TEST_DIAG") == "1"
    llm_kwargs = dict(kwargs)
    if runner is None and "runner" in llm_kwargs:
        llm_kwargs.pop("runner", None)
    if should_diag:
        try:
            log_vllm_diagnostics("before-create-llm")
        except Exception:
            pass
    llm = _create_llm_autotune(model=model, **llm_kwargs)
    if should_diag:
        try:
            log_vllm_diagnostics("after-create-llm")
        except Exception:
            pass
    return llm


def create_async_engine(model: str, *, engine_mode: str = "0", **kwargs):
    # Reduce default KV cache footprint in CI unless caller overrides
    kwargs.setdefault("max_model_len", 256)
    # Constrain scheduler concurrency on CI to reduce memory pressure
    if os.environ.get("CI") == "true":
        kwargs.setdefault("max_num_batched_tokens", 256)
        kwargs.setdefault("max_num_seqs", 1)
    # Respect explicit gpu_memory_utilization if provided
    explicit_util = kwargs.pop("gpu_memory_utilization", None)
    util_candidates = kwargs.pop(
        "gpu_util_candidates",
        [0.05, 0.1, 0.2, 0.3, 0.5, 0.7, 0.8, 0.9, 0.95],
    )
    if explicit_util is not None:
        util_candidates = [explicit_util]
    last_error = None
    should_diag = os.environ.get("DD_VLLM_TEST_DIAG") == "1"
    if should_diag:
        try:
            log_vllm_diagnostics("before-create-async-engine")
        except Exception:
            pass
    for util in util_candidates:
        try:
            args = AsyncEngineArgs(model=model, gpu_memory_utilization=util, **kwargs)
            if engine_mode == "1":
                from vllm.v1.engine.async_llm import AsyncLLM as _Async  # type: ignore
            else:
                from vllm.engine.async_llm_engine import AsyncLLMEngine as _Async  # type: ignore
            engine = _Async.from_engine_args(args)
            if should_diag:
                try:
                    log_vllm_diagnostics("after-create-async-engine")
                except Exception:
                    pass
            return engine
        except Exception as exc:  # pragma: no cover
            last_error = exc
            continue
    raise last_error


def get_simple_chat_template() -> str:
    return (
        "{% for message in messages %}"
        "{% if message['role'] == 'system' %}{{ message['content'] }}\n"
        "{% elif message['role'] == 'user' %}User: {{ message['content'] }}\n"
        "{% elif message['role'] == 'assistant' %}Assistant: {{ message['content'] }}\n"
        "{% endif %}"
        "{% endfor %}"
        "Assistant:"
    )


def shutdown_cached_llms() -> None:
    # Retained for backwards compatibility; no-op since caching is disabled
    try:
        import gc  # type: ignore

        gc.collect()
    except Exception:
        pass
    try:
        import torch  # type: ignore

        if hasattr(torch, "cuda"):
            torch.cuda.empty_cache()
    except Exception:
        pass


def log_vllm_diagnostics(label: str) -> None:
    """Print CPU/GPU and environment diagnostics to stdout when debugging.

    Safe to call on hosts without GPUs or nvidia-smi.
    """
    try:
        print(f"---DIAG START: {label}---")
        # Environment snapshot
        for k in [
            "CI",
            "VLLM_USE_V1",
            "VLLM_USE_MQ",
            "VLLM_GPU_UTIL",
            "PROMETHEUS_MULTIPROC_DIR",
            "PYTEST_CURRENT_TEST",
        ]:
            v = os.environ.get(k)
            if v is not None:
                print(f"ENV {k}={v}")

        # Process limits and usage
        try:
            import resource  # type: ignore

            nofile = resource.getrlimit(resource.RLIMIT_NOFILE)
            print(f"RLIMIT_NOFILE={nofile}")
        except Exception:
            pass
        try:
            fd_count = len(os.listdir(f"/proc/{os.getpid()}/fd"))
            print(f"FD_COUNT={fd_count}")
        except Exception:
            pass
        try:
            with open("/proc/self/status", "r", encoding="utf-8", errors="ignore") as f:
                status = f.read()
            for line in status.splitlines():
                if line.startswith(("VmRSS:", "Threads:", "FDSize:")):
                    print(f"PROC {line}")
        except Exception:
            pass

        # System memory snapshot
        try:
            with open("/proc/meminfo", "r", encoding="utf-8", errors="ignore") as f:
                meminfo = f.read().splitlines()
            for line in meminfo[:10]:
                print(f"MEMINFO {line}")
        except Exception:
            pass

        # cgroup (container) memory limits and usage (v2 and v1)
        try:
            cg_base = "/sys/fs/cgroup"
            # cgroup v2
            v2_current = os.path.join(cg_base, "memory.current")
            v2_max = os.path.join(cg_base, "memory.max")
            if os.path.exists(v2_current) and os.path.exists(v2_max):
                with open(v2_current, "r", encoding="utf-8", errors="ignore") as f:
                    cur = f.read().strip()
                with open(v2_max, "r", encoding="utf-8", errors="ignore") as f:
                    mx = f.read().strip()
                print(f"CGROUPv2 memory.current={cur} memory.max={mx}")
            # cgroup v1
            v1_dir = os.path.join(cg_base, "memory")
            v1_cur = os.path.join(v1_dir, "memory.usage_in_bytes")
            v1_lim = os.path.join(v1_dir, "memory.limit_in_bytes")
            if os.path.exists(v1_cur) and os.path.exists(v1_lim):
                with open(v1_cur, "r", encoding="utf-8", errors="ignore") as f:
                    cur = f.read().strip()
                with open(v1_lim, "r", encoding="utf-8", errors="ignore") as f:
                    lim = f.read().strip()
                print(f"CGROUPv1 memory.usage_in_bytes={cur} memory.limit_in_bytes={lim}")
            # Show which cgroups we're in
            try:
                with open("/proc/self/cgroup", "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.read().strip().splitlines()
                for line in lines[:10]:
                    print(f"CGROUP {line}")
            except Exception:
                pass
        except Exception:
            pass

        # GPU info via torch
        try:
            import torch  # type: ignore

            if hasattr(torch, "cuda") and torch.cuda.is_available():
                num = torch.cuda.device_count()
                for i in range(num):
                    free, total = torch.cuda.mem_get_info(i)
                    used = total - free
                    print(f"CUDA dev{i} total={total//(1024**2)}MB used={used//(1024**2)}MB free={free//(1024**2)}MB")
                try:
                    summ = torch.cuda.memory_summary(device=None, abbreviated=True)
                    head = "\n".join(summ.splitlines()[:50])
                    print(head)
                except Exception:
                    pass
        except Exception:
            pass

        # GPU info via nvidia-smi
        try:
            import subprocess  # type: ignore

            out = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=index,name,memory.total,memory.used,memory.free",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout:
                for line in out.stdout.strip().splitlines():
                    print(f"NVSMI GPU {line}")
            procs = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-compute-apps=pid,process_name,used_memory",
                    "--format=csv,noheader,nounits",
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if procs.returncode == 0 and procs.stdout:
                for line in procs.stdout.strip().splitlines():
                    print(f"NVSMI PROC {line}")
        except Exception:
            pass

        # Prometheus multiprocess dir
        pdir = os.environ.get("PROMETHEUS_MULTIPROC_DIR")
        if pdir and os.path.isdir(pdir):
            try:
                files = os.listdir(pdir)
                print(f"PROM_DIR files={len(files)} -> {files[:10]}")
            except Exception:
                pass

        # Top processes by RSS inside the container
        try:
            import subprocess  # type: ignore

            out = subprocess.run(
                ["bash", "-lc", "ps -eo pid,comm,rss,vsz,%mem --sort=-rss | head -n 12"],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if out.returncode == 0 and out.stdout:
                print("PS TOP RSS:\n" + out.stdout.rstrip())
        except Exception:
            pass

        print(f"---DIAG END: {label}---")
    except Exception:
        pass
