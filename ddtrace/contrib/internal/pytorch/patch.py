import torch

from ddtrace.internal.logger import get_logger


log = get_logger(__name__)


def get_version() -> str:
    # torch.__version__ is a `TorchVersion` (a str subclass); the contrib test
    # harness checks `type(version) == str`, so cast to a plain str here.
    return str(getattr(torch, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"torch": ">=2.0,<2.4"}


def patch() -> None:
    if getattr(torch, "_datadog_patch", False):
        return
    torch._datadog_patch = True
    # Imported inside patch() so the module-level import of `_distributed`
    # doesn't pull in `torch.distributed.*` symbols at module import time.
    from ddtrace.contrib.internal.pytorch import _distributed

    _distributed.install()


def unpatch() -> None:
    if not getattr(torch, "_datadog_patch", False):
        return
    torch._datadog_patch = False
    from ddtrace.contrib.internal.pytorch import _distributed

    _distributed.uninstall()
    # Tear down the Layer 3 profiler. Always called: `shutdown_profiler` is a
    # no-op when no profiler is running, so we don't gate on the current value
    # of DD_PYTORCH_KERNEL_PROFILING (which may have changed since patch()).
    try:
        from ddtrace.contrib.internal.pytorch._profiler import shutdown_profiler

        shutdown_profiler()
    except Exception:
        log.debug("pytorch: Layer 3 shutdown raised; suppressing", exc_info=True)
