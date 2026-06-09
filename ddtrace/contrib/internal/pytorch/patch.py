import torch

from ddtrace.internal.logger import get_logger
from ddtrace.internal.utils.version import parse_version


log = get_logger(__name__)

TORCH_VERSION = parse_version(str(getattr(torch, "__version__", "")))


def get_version() -> str:
    # torch.__version__ is a `TorchVersion` (a str subclass); the contrib test
    # harness checks `type(version) == str`, so cast to a plain str here.
    return str(getattr(torch, "__version__", ""))


def _supported_versions() -> dict[str, str]:
    return {"torch": ">=2.0"}


def patch() -> None:
    if getattr(torch, "_datadog_patch", False):
        return
    if TORCH_VERSION < (2, 0, 0) or TORCH_VERSION >= (2, 4, 0):
        log.warning(
            "pytorch: torch version %s is not supported (supported: >=2.0,<2.4); skipping instrumentation",
            torch.__version__,
        )
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
