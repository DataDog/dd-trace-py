import pytest

import ddtrace.contrib.internal.pytorch.patch as pytorch_patch
from ddtrace.contrib.internal.pytorch.patch import get_version
from ddtrace.contrib.internal.pytorch.patch import patch
from ddtrace.contrib.internal.pytorch.patch import unpatch
from tests.contrib.patch import PatchTestCase


class TestPyTorchPatch(PatchTestCase.Base):
    __integration_name__ = "pytorch"
    __module_name__ = "torch"
    __patch_func__ = patch
    __unpatch_func__ = unpatch
    __get_version__ = get_version

    def assert_module_patched(self, torch):
        assert getattr(torch, "_datadog_patch", False) is True

    def assert_not_module_patched(self, torch):
        assert getattr(torch, "_datadog_patch", False) is False

    def assert_not_module_double_patched(self, torch):
        assert getattr(torch, "_datadog_patch", False) is True


@pytest.mark.filterwarnings("ignore::DeprecationWarning")
def test_patch_all_does_not_enable_pytorch_by_default(monkeypatch):
    """Pytorch is opt-in: a plain patch_all() must not flip torch._datadog_patch."""
    import torch

    from ddtrace._monkey import PATCH_MODULES

    assert PATCH_MODULES.get("pytorch") is False

    if getattr(torch, "_datadog_patch", False):
        from ddtrace.contrib.internal.pytorch.patch import unpatch

        unpatch()

    from ddtrace._monkey import patch_all

    patch_all()
    assert getattr(torch, "_datadog_patch", False) is False


def test_explicit_patch_pytorch_true_still_works():
    import torch

    from ddtrace._monkey import patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch

    if getattr(torch, "_datadog_patch", False):
        unpatch()

    patch(pytorch=True)
    try:
        assert getattr(torch, "_datadog_patch", False) is True
    finally:
        unpatch()


@pytest.mark.parametrize("bad_version", [(1, 9, 0), (2, 4, 0), (3, 0, 0)])
def test_patch_skipped_for_unsupported_torch_version(monkeypatch, bad_version):
    import torch

    if getattr(torch, "_datadog_patch", False):
        unpatch()

    monkeypatch.setattr(pytorch_patch, "TORCH_VERSION", bad_version)
    patch()
    assert getattr(torch, "_datadog_patch", False) is False


def test_install_runs_unconditionally(monkeypatch):
    import torch

    from ddtrace.contrib.internal.pytorch import _distributed
    from ddtrace.contrib.internal.pytorch.patch import patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch

    monkeypatch.delenv("RANK", raising=False)
    monkeypatch.delenv("WORLD_SIZE", raising=False)
    monkeypatch.setattr(torch.distributed, "is_initialized", lambda: False)

    if getattr(torch, "_datadog_patch", False):
        unpatch()

    patch()
    try:
        assert _distributed._installed is True
    finally:
        unpatch()
