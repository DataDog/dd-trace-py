import pytest

import ddtrace.contrib.internal.pytorch.patch as pytorch_patch
from ddtrace.contrib.internal.pytorch.patch import patch
from ddtrace.contrib.internal.pytorch.patch import unpatch
from tests.contrib.patch_scenarios import PATCH_SCENARIOS


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


@pytest.mark.parametrize("bad_version", [(1, 9, 0), (3, 0, 0)])
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


@pytest.mark.parametrize("scenario", PATCH_SCENARIOS)
def test_TestPyTorchPatch(python_process, scenario):
    module = "tests.contrib.pytorch"
    module += "._test_pytorch_patch_scenarios"
    case = "TestPyTorchPatch"
    result = python_process(
        f"from tests.contrib.patch import run_scenario; run_scenario({module!r}, {case!r}, {scenario!r})"
    )
    if result.returncode == 77:
        pytest.skip(result.stdout.decode())
    if result.returncode == 78:
        pytest.xfail(result.stdout.decode())
    result.check_returncode()
