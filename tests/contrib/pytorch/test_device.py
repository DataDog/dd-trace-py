"""Device-id discovery for Layer Zero metric tagging."""

import builtins
from unittest import mock

import pytest

from ddtrace.contrib.internal.pytorch import _device
from ddtrace.contrib.internal.pytorch import _test_helpers as _th


@pytest.fixture(autouse=True)
def _reset_cache():
    _th.reset_device_cache()
    yield
    _th.reset_device_cache()


def test_returns_cuda_uuid_when_available():
    with (
        mock.patch.object(_device, "_query_cuda_uuid", return_value="GPU-abc-uuid") as uuid_mock,
        mock.patch.object(_device, "_cuda_is_available", return_value=True),
        mock.patch.object(_device, "_cuda_index", return_value=3),
    ):
        info = _device.discover(local_rank=3)
    assert info.device_id == "GPU-abc-uuid"
    assert info.device_index == 3
    assert info.kind == "cuda"
    # `_query_cuda_uuid` must be called with the same idx recorded as
    # `device_index` — otherwise UUID and device_index refer to different
    # physical GPUs on non-zero ranks.
    uuid_mock.assert_called_once_with(3)


def test_falls_back_to_host_cuda_when_uuid_unavailable():
    with (
        mock.patch.object(_device, "_query_cuda_uuid", return_value=None),
        mock.patch.object(_device, "_cuda_is_available", return_value=True),
        mock.patch.object(_device, "_cuda_index", return_value=5),
        mock.patch.object(_device, "_hostname", return_value="h-42"),
    ):
        info = _device.discover(local_rank=5)
    assert info.device_id == "h-42:cuda:5"
    assert info.device_index == 5
    assert info.kind == "cuda"


def test_falls_back_to_host_cpu_when_no_cuda():
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-42"),
    ):
        info = _device.discover(local_rank=2)
    assert info.device_id == "h-42:cpu"
    assert info.device_index is None
    assert info.kind == "cpu"


def test_cuda_index_unavailable_yields_unknown_device():
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=True),
        mock.patch.object(_device, "_cuda_index", return_value=None),
        mock.patch.object(_device, "_hostname", return_value="h-1"),
    ):
        info = _device.discover(local_rank=0)
    assert info.device_id == "h-1:cuda:unknown"
    assert info.device_index is None
    assert info.kind == "cuda"


def test_discover_is_cached():
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-42") as host_mock,
    ):
        _device.discover(local_rank=0)
        _device.discover(local_rank=0)
    assert host_mock.call_count == 1


def test_get_returns_none_before_discover():
    assert _device.get() is None


def test_get_returns_cached_after_discover():
    with (
        mock.patch.object(_device, "_cuda_is_available", return_value=False),
        mock.patch.object(_device, "_hostname", return_value="h-42"),
    ):
        info = _device.discover(local_rank=0)
    assert _device.get() is info


def test_query_cuda_uuid_uses_pynvml_with_correct_idx():
    """End-to-end CUDA discovery path: `_query_cuda_uuid` calls pynvml
    with the right idx and decodes its bytes response.
    """
    fake_pynvml = mock.MagicMock()
    fake_pynvml.nvmlDeviceGetHandleByIndex.return_value = "handle-7"
    fake_pynvml.nvmlDeviceGetUUID.return_value = b"GPU-fake-uuid-from-pynvml"

    with (
        mock.patch.dict("sys.modules", {"pynvml": fake_pynvml}),
        mock.patch.object(_device, "_cuda_is_available", return_value=True),
        mock.patch.object(_device, "_cuda_index", return_value=7),
        mock.patch.object(_device, "_hostname", return_value="h-1"),
        mock.patch.object(_device, "_query_cuda_props", return_value={}),
        mock.patch.object(_device, "_query_cuda_driver_version", return_value=None),
    ):
        info = _device.discover(local_rank=7)

    fake_pynvml.nvmlInit.assert_called_once()
    fake_pynvml.nvmlDeviceGetHandleByIndex.assert_called_once_with(7)
    fake_pynvml.nvmlDeviceGetUUID.assert_called_once_with("handle-7")
    fake_pynvml.nvmlShutdown.assert_called_once()
    assert info.device_id == "GPU-fake-uuid-from-pynvml"
    assert info.device_index == 7
    assert info.kind == "cuda"


def test_query_cuda_uuid_falls_back_to_torch_when_pynvml_missing():
    """If pynvml is unavailable, `_query_cuda_uuid` falls back to
    `torch.cuda.get_device_properties(idx).uuid`.
    """
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pynvml":
            raise ImportError("pynvml not available")
        return real_import(name, *args, **kwargs)

    fake_props = mock.Mock()
    fake_props.uuid = "GPU-from-torch-props"
    with (
        mock.patch.object(builtins, "__import__", side_effect=fake_import),
        mock.patch.object(_device, "_cuda_is_available", return_value=True),
        mock.patch.object(_device, "_cuda_index", return_value=2),
        mock.patch.object(_device, "_hostname", return_value="h-1"),
        mock.patch.object(_device, "_query_cuda_props", return_value={}),
        mock.patch.object(_device, "_query_cuda_driver_version", return_value=None),
    ):
        import torch

        with mock.patch.object(torch.cuda, "get_device_properties", return_value=fake_props):
            info = _device.discover(local_rank=2)
    assert info.device_id == "GPU-from-torch-props"
    assert info.device_index == 2


def test_query_cuda_props_returns_expected_fields(monkeypatch):
    import torch

    class FakeProps:
        name = "A100"
        major = 8
        minor = 0
        multi_processor_count = 108
        total_memory = 42 * 1024**3

    monkeypatch.setattr(torch.cuda, "get_device_properties", lambda idx: FakeProps())
    props = _device._query_cuda_props(0)
    assert props["gpu_name"] == "A100"
    assert props["gpu_compute_capability"] == "8.0"
    assert props["gpu_sm_count"] == 108
    assert props["gpu_total_memory_bytes"] == 42 * 1024**3


def test_query_cuda_props_returns_empty_dict_on_exception(monkeypatch):
    import torch

    monkeypatch.setattr(torch.cuda, "get_device_properties", lambda idx: (_ for _ in ()).throw(RuntimeError("no gpu")))
    props = _device._query_cuda_props(0)
    assert props == {}


def test_query_cuda_props_omits_partial_fields(monkeypatch):
    """Fields absent on the props object are not included in the result dict."""
    import torch

    class MinimalProps:
        name = "V100"
        # major/minor absent → gpu_compute_capability should be omitted
        # multi_processor_count absent → gpu_sm_count omitted
        # total_memory absent → gpu_total_memory_bytes omitted

    monkeypatch.setattr(torch.cuda, "get_device_properties", lambda idx: MinimalProps())
    props = _device._query_cuda_props(0)
    assert props.get("gpu_name") == "V100"
    assert "gpu_compute_capability" not in props
    assert "gpu_sm_count" not in props
    assert "gpu_total_memory_bytes" not in props


def test_query_cuda_driver_version_decodes_bytes():
    fake_pynvml = mock.MagicMock()
    fake_pynvml.nvmlSystemGetDriverVersion.return_value = b"535.86.10"

    with mock.patch.dict("sys.modules", {"pynvml": fake_pynvml}):
        version = _device._query_cuda_driver_version()

    assert version == "535.86.10"
    fake_pynvml.nvmlInit.assert_called_once()
    fake_pynvml.nvmlShutdown.assert_called_once()


def test_query_cuda_driver_version_returns_none_when_pynvml_missing():
    real_import = builtins.__import__

    def fake_import(name, *args, **kwargs):
        if name == "pynvml":
            raise ImportError("pynvml not available")
        return real_import(name, *args, **kwargs)

    with mock.patch.object(builtins, "__import__", side_effect=fake_import):
        version = _device._query_cuda_driver_version()

    assert version is None


def test_discover_populates_gpu_fields(monkeypatch):
    monkeypatch.setattr(_device, "_cuda_is_available", lambda: True)
    monkeypatch.setattr(_device, "_cuda_index", lambda local_rank: 0)
    monkeypatch.setattr(_device, "_query_cuda_uuid", lambda idx: "GPU-ABC")
    monkeypatch.setattr(
        _device,
        "_query_cuda_props",
        lambda idx: {
            "gpu_name": "A100",
            "gpu_compute_capability": "8.0",
            "gpu_sm_count": 108,
            "gpu_total_memory_bytes": 84 * 1024**3,
        },
    )
    monkeypatch.setattr(_device, "_query_cuda_driver_version", lambda: "535.86.10")
    _device._cache = None

    info = _device.discover(local_rank=0)
    assert info.device_id == "GPU-ABC"
    assert info.gpu_name == "A100"
    assert info.gpu_compute_capability == "8.0"
    assert info.gpu_sm_count == 108
    assert info.gpu_total_memory_bytes == 84 * 1024**3
    assert info.gpu_driver_version == "535.86.10"


def test_discover_cpu_path_gpu_fields_are_none(monkeypatch):
    """CPU path must leave all GPU fields as None (no regression)."""
    monkeypatch.setattr(_device, "_cuda_is_available", lambda: False)
    monkeypatch.setattr(_device, "_hostname", lambda: "h-cpu")
    _device._cache = None

    info = _device.discover(local_rank=0)
    assert info.kind == "cpu"
    assert info.gpu_name is None
    assert info.gpu_compute_capability is None
    assert info.gpu_sm_count is None
    assert info.gpu_total_memory_bytes is None
    assert info.gpu_driver_version is None


def test_discover_cuda_unknown_index_gpu_fields_are_none(monkeypatch):
    """When device index is unknown, GPU fields remain None."""
    monkeypatch.setattr(_device, "_cuda_is_available", lambda: True)
    monkeypatch.setattr(_device, "_cuda_index", lambda local_rank: None)
    monkeypatch.setattr(_device, "_hostname", lambda: "h-1")
    _device._cache = None

    info = _device.discover(local_rank=0)
    assert info.device_id == "h-1:cuda:unknown"
    assert info.gpu_name is None
    assert info.gpu_driver_version is None
