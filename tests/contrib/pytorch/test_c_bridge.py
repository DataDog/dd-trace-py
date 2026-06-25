"""Unit tests for _c_bridge.build_init_bundle()."""

from unittest import mock

from ddtrace.contrib.internal.pytorch import _c_bridge


def test_bundle_includes_torch_version(monkeypatch):
    fake_torch = mock.MagicMock()
    fake_torch.version.__version__ = "2.3.1+cu121"
    fake_torch.cuda.is_available.return_value = True
    fake_torch.cuda.runtime_version = lambda: 12010
    fake_torch.cuda.is_initialized.return_value = True
    fake_torch.backends.cudnn.is_available.return_value = True
    fake_torch.backends.cudnn.enabled = True
    fake_torch.backends.cudnn.benchmark = False
    fake_torch.backends.cudnn.deterministic = False
    fake_torch.backends.cudnn.version.return_value = 8902
    fake_torch.get_float32_matmul_precision = lambda: "highest"
    fake_torch.backends.mps.is_available = lambda: False
    fake_torch.distributed.is_initialized.return_value = True
    fake_torch.distributed.get_backend.return_value = "nccl"
    monkeypatch.setattr(_c_bridge, "_import_torch", lambda: fake_torch)

    bundle = _c_bridge.build_init_bundle(model=None)
    assert bundle["torch.version"] == "2.3.1+cu121"
    assert bundle["torch.cudnn.enabled"] == "true"
    assert bundle["torch.cudnn.benchmark"] == "false"
    assert bundle["torch.cudnn.deterministic"] == "false"
    assert bundle["torch.cudnn.version"] == "8902"
    assert bundle["torch.float32_matmul_precision"] == "highest"
    assert bundle["torch.distributed.backend"] == "nccl"


def test_bundle_silent_when_torch_missing(monkeypatch):
    def boom():
        raise ImportError("no torch")

    monkeypatch.setattr(_c_bridge, "_import_torch", boom)

    bundle = _c_bridge.build_init_bundle(model=None)
    assert isinstance(bundle, dict)
    assert "torch.version" not in bundle


def test_bundle_includes_ray_metadata_when_present(monkeypatch):
    fake_torch = mock.MagicMock()
    fake_torch.distributed.is_initialized.return_value = False
    monkeypatch.setattr(_c_bridge, "_import_torch", lambda: fake_torch)
    monkeypatch.setenv("RAY_JOB_ID", "raysubmit_test")

    bundle = _c_bridge.build_init_bundle(
        model=None,
        ray_run_name="myrun",
        ray_submission_id="raysubmit_test",
    )
    assert bundle["ray.train.run_name"] == "myrun"
    assert bundle["ray.submission_id"] == "raysubmit_test"


def test_bundle_includes_model_invariants_when_module_provided(monkeypatch):
    fake_torch = mock.MagicMock()
    monkeypatch.setattr(_c_bridge, "_import_torch", lambda: fake_torch)

    class FakeParam:
        def __init__(self, n, requires_grad=True):
            self._n = n
            self.requires_grad = requires_grad
            self.dtype = "torch.float32"

        def numel(self):
            return self._n

    class FakeModule:
        def parameters(self):
            return [FakeParam(1_000_000), FakeParam(500_000, requires_grad=False)]

        def modules(self):
            return [self]

    bundle = _c_bridge.build_init_bundle(model=FakeModule())
    assert bundle["model.param_count"] == "1500000"
    assert bundle["model.trainable_param_count"] == "1000000"


def test_bundle_unwraps_deepspeed_engine(monkeypatch):
    """DeepSpeed users pass the engine; the underlying nn.Module is at .module."""
    fake_torch = mock.MagicMock()
    monkeypatch.setattr(_c_bridge, "_import_torch", lambda: fake_torch)

    class FakeParam:
        def __init__(self, n):
            self._n = n
            self.requires_grad = True
            self.dtype = "torch.float16"

        def numel(self):
            return self._n

    class InnerModule:
        def parameters(self):
            return [FakeParam(7_000_000_000)]

        def modules(self):
            return [self]

    class FakeDeepSpeedEngine:
        def __init__(self):
            self.module = InnerModule()

    FakeDeepSpeedEngine.__name__ = "DeepSpeedEngine"
    engine = FakeDeepSpeedEngine()

    bundle = _c_bridge.build_init_bundle(model=engine)
    assert bundle["model.param_count"] == "7000000000"
    assert bundle["framework"] == "deepspeed"


def test_bundle_framework_left_unset_for_plain_module(monkeypatch):
    """Plain nn.Module — bundle should not set framework (upstream decides DDP/FSDP)."""
    fake_torch = mock.MagicMock()
    monkeypatch.setattr(_c_bridge, "_import_torch", lambda: fake_torch)

    class FakeModule:
        def parameters(self):
            return []

        def modules(self):
            return [self]

    bundle = _c_bridge.build_init_bundle(model=FakeModule())
    assert "framework" not in bundle


def test_bundle_does_not_override_upstream_framework(monkeypatch):
    """When upstream already set framework=ddp, the DeepSpeed unwrap path
    must NOT overwrite it (DeepSpeed often wraps a DDP module)."""
    fake_torch = mock.MagicMock()
    monkeypatch.setattr(_c_bridge, "_import_torch", lambda: fake_torch)

    class FakeParam:
        def __init__(self, n):
            self._n = n
            self.requires_grad = True
            self.dtype = "torch.float32"

        def numel(self):
            return self._n

    class InnerModule:
        def parameters(self):
            return [FakeParam(1_000_000)]

        def modules(self):
            return [self]

    class FakeDeepSpeedEngine:
        def __init__(self):
            self.module = InnerModule()

    FakeDeepSpeedEngine.__name__ = "DeepSpeedEngine"

    bundle = _c_bridge.build_init_bundle(
        model=FakeDeepSpeedEngine(),
        framework_already_set=True,
    )
    assert "framework" not in bundle


def test_bundle_never_exceeds_64_keys(monkeypatch):
    fake_torch = mock.MagicMock()
    monkeypatch.setattr(_c_bridge, "_import_torch", lambda: fake_torch)
    bundle = _c_bridge.build_init_bundle(model=None)
    assert len(bundle) <= 64
