import importlib
import sys
import types
from unittest import mock

import torch

from ddtrace.contrib.internal.pytorch._utils import _FRAMEWORK_REGISTRY
from ddtrace.contrib.internal.pytorch._utils import _get_active_framework
from ddtrace.contrib.internal.pytorch.patch import patch
from ddtrace.contrib.internal.pytorch.patch import unpatch


def _enable_l2(monkeypatch):
    """Set DD_PYTORCH_PROFILING=true and reload _hooks so PROFILING_ENABLED is True."""
    monkeypatch.setenv("DD_PYTORCH_PROFILING", "true")
    import ddtrace.contrib.internal.pytorch._hooks as hooks_mod

    importlib.reload(hooks_mod)


def _disable_l2(monkeypatch):
    """Unset DD_PYTORCH_PROFILING and reload _hooks so PROFILING_ENABLED is False."""
    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    import ddtrace.contrib.internal.pytorch._hooks as hooks_mod

    importlib.reload(hooks_mod)


def test_ddp_init_registers_framework(monkeypatch):
    # Replace the real DDP.__init__ with a no-op BEFORE patch() so our wrapper
    # wraps the no-op rather than the real (process-group-requiring) ctor.
    monkeypatch.setattr(
        torch.nn.parallel.DistributedDataParallel,
        "__init__",
        lambda self, *a, **k: None,
    )
    patch()
    try:
        ddp = torch.nn.parallel.DistributedDataParallel.__new__(torch.nn.parallel.DistributedDataParallel)
        torch.nn.parallel.DistributedDataParallel.__init__(ddp, mock.Mock())
        assert _FRAMEWORK_REGISTRY.get(ddp) == "ddp"
    finally:
        unpatch()


def test_ddp_unpatch_restores_init(monkeypatch):
    def original(self, *a, **k):
        return None

    monkeypatch.setattr(torch.nn.parallel.DistributedDataParallel, "__init__", original)
    patch()
    # After patch, __init__ is wrapped.
    assert torch.nn.parallel.DistributedDataParallel.__init__ is not original
    unpatch()
    # After unpatch, the wrapper is gone (the unwrapped attribute may differ
    # by identity depending on wrapt's wrap mechanism — assert by absence of
    # registration side effect instead).
    ddp = torch.nn.parallel.DistributedDataParallel.__new__(torch.nn.parallel.DistributedDataParallel)
    torch.nn.parallel.DistributedDataParallel.__init__(ddp, mock.Mock())
    assert _FRAMEWORK_REGISTRY.get(ddp) is None


def test_ddp_init_handles_register_failure_gracefully(monkeypatch, caplog):
    """If register_framework raises (e.g. instance lacks __weakref__), the
    original __init__ result is still returned and the user's code continues.
    """
    monkeypatch.setattr(
        torch.nn.parallel.DistributedDataParallel,
        "__init__",
        lambda self, *a, **k: None,
    )
    monkeypatch.setattr(
        "ddtrace.contrib.internal.pytorch._distributed.register_framework",
        mock.Mock(side_effect=TypeError("cannot create weak reference")),
    )
    patch()
    try:
        with caplog.at_level("WARNING"):
            ddp = torch.nn.parallel.DistributedDataParallel.__new__(torch.nn.parallel.DistributedDataParallel)
            # Must not raise.
            torch.nn.parallel.DistributedDataParallel.__init__(ddp, mock.Mock())
        assert any("DDP framework" in rec.message for rec in caplog.records)
    finally:
        unpatch()


def test_fsdp_init_registers_framework(monkeypatch):
    from torch.distributed.fsdp import FullyShardedDataParallel

    monkeypatch.setattr(FullyShardedDataParallel, "__init__", lambda self, *a, **k: None)
    patch()
    try:
        fsdp = FullyShardedDataParallel.__new__(FullyShardedDataParallel)
        FullyShardedDataParallel.__init__(fsdp, mock.Mock())
        assert _FRAMEWORK_REGISTRY.get(fsdp) == "fsdp"
    finally:
        unpatch()


def test_fsdp_forward_enters_framework_context(monkeypatch):
    from torch.distributed.fsdp import FullyShardedDataParallel

    from ddtrace.contrib.internal.pytorch._utils import register_framework

    captured = {}

    def fake_forward(self, *a, **kw):
        captured["fw"] = _get_active_framework()
        return None

    monkeypatch.setattr(FullyShardedDataParallel, "forward", fake_forward)
    patch()
    try:
        fsdp = FullyShardedDataParallel.__new__(FullyShardedDataParallel)
        register_framework(fsdp, "fsdp")
        FullyShardedDataParallel.forward(fsdp)
        assert captured["fw"] == "fsdp"
    finally:
        unpatch()


def test_deepspeed_install_skips_cleanly_when_absent(monkeypatch):
    # Make `import deepspeed` raise.
    monkeypatch.setitem(sys.modules, "deepspeed", None)
    patch()
    unpatch()  # must round-trip without raising


def test_deepspeed_wrapper_coexists_with_external_monkey_patch(monkeypatch):
    """Simulate DeepSpeed-style monkey-patching applied after ours and assert
    both our framework registration AND the external hook fire.
    """
    fake_module = types.ModuleType("deepspeed")

    class DeepSpeedEngine:
        def __init__(self, *a, **k):
            pass

        def step(self):
            return "real"

    fake_module.DeepSpeedEngine = DeepSpeedEngine
    monkeypatch.setitem(sys.modules, "deepspeed", fake_module)

    patch()
    try:
        # External code wraps step *after* our patch.
        original_step = DeepSpeedEngine.step
        ext_called = []

        def external(self):
            ext_called.append("external")
            return original_step(self)

        DeepSpeedEngine.step = external

        engine = DeepSpeedEngine()
        # Our patched __init__ should have registered the framework.
        assert _FRAMEWORK_REGISTRY.get(engine) == "deepspeed"
        engine.step()
        assert ext_called == ["external"]
    finally:
        unpatch()


def test_optimizer_step_is_wrapped_on_instance(monkeypatch):
    from ddtrace.contrib.internal.pytorch._distributed import _step_originals

    _enable_l2(monkeypatch)
    patch()
    try:
        params = [torch.nn.Parameter(torch.zeros(2))]
        opt = torch.optim.SGD(params, lr=0.01)
        assert opt in _step_originals
        opt.step()  # pass-through; must not raise
    finally:
        unpatch()
        _disable_l2(monkeypatch)


def test_optimizer_unpatch_restores_step(monkeypatch):
    from ddtrace.contrib.internal.pytorch._distributed import _step_originals

    _enable_l2(monkeypatch)
    patch()
    params = [torch.nn.Parameter(torch.zeros(2))]
    opt = torch.optim.SGD(params, lr=0.01)
    assert opt in _step_originals
    unpatch()
    _disable_l2(monkeypatch)
    # After unpatch, our registry is cleared and `step` is restored.
    assert opt not in _step_originals


def test_optimizer_subclass_step_is_wrapped(monkeypatch):
    """AdamW (a subclass) overrides step; instance-level wrapping must still apply."""
    from ddtrace.contrib.internal.pytorch._distributed import _step_originals

    _enable_l2(monkeypatch)
    patch()
    try:
        params = [torch.nn.Parameter(torch.zeros(2))]
        adamw = torch.optim.AdamW(params, lr=1e-3)
        assert adamw in _step_originals
    finally:
        unpatch()
        _disable_l2(monkeypatch)


def test_gradscaler_cuda_amp_is_wrapped(monkeypatch):
    _enable_l2(monkeypatch)
    patch()
    try:
        # `.step` is wrapped via class-attribute replacement; verify the
        # wrapper sees the in_amp toggle by calling through.
        from ddtrace.contrib.internal.pytorch._utils import is_amp_step_in_progress as _is_amp_step_in_progress

        called_with_amp_on = []

        def fake_step(self, optimizer, *args, **kwargs):
            called_with_amp_on.append(_is_amp_step_in_progress())
            return None

        monkeypatch.setattr(torch.cuda.amp.GradScaler, "step", fake_step, raising=True)
        # Re-patch after monkeypatch so our wrapper wraps the fake.
        unpatch()
        patch()
        scaler = torch.cuda.amp.GradScaler.__new__(torch.cuda.amp.GradScaler)
        scaler.step(mock.Mock())
        assert called_with_amp_on == [True]
        assert _is_amp_step_in_progress() is False
    finally:
        unpatch()


def test_gradscaler_alias_dedup_does_not_double_wrap(monkeypatch):
    """In torch >= 2.1 `torch.amp.GradScaler` and `torch.cuda.amp.GradScaler`
    are typically the same class object. Wrapping `.step` on both would
    corrupt the `in_amp` toggle (it would flip twice). The integration
    dedup by identity.
    """
    cuda_cls = torch.cuda.amp.GradScaler
    amp_cls = getattr(torch.amp, "GradScaler", None)
    if amp_cls is None or amp_cls is not cuda_cls:
        import pytest

        pytest.skip("torch.amp.GradScaler not aliased to torch.cuda.amp.GradScaler on this torch")
    patch()
    try:
        # If double-wrapped, `__wrapped__.__wrapped__` would also exist.
        step = cuda_cls.step
        assert getattr(step, "__wrapped__", None) is not None
        assert getattr(step.__wrapped__, "__wrapped__", None) is None
    finally:
        unpatch()


def test_register_comm_hook_chains_user_hook_with_state(monkeypatch):
    seen = {}

    def user_hook(state, bucket):
        from ddtrace.contrib.internal.pytorch._utils import is_instrumentation_bypassed

        seen["state"] = state
        seen["bucket"] = bucket
        seen["bypassed"] = is_instrumentation_bypassed()
        fut = mock.Mock()
        return fut

    captured = {}

    def fake_register(self, state, hook):
        captured["state"] = state
        captured["hook"] = hook

    monkeypatch.setattr(torch.nn.parallel.DistributedDataParallel, "register_comm_hook", fake_register)
    patch()
    try:
        ddp = torch.nn.parallel.DistributedDataParallel.__new__(torch.nn.parallel.DistributedDataParallel)
        user_state = {"power_sgd_state": "value"}
        ddp.register_comm_hook(user_state, user_hook)
        # user_state passed through unchanged (NOT replaced with None).
        assert captured["state"] is user_state
        # Invoking the chained hook calls our timing layer + user hook with bypass.
        bucket = mock.Mock()
        bucket.gradients = lambda: []
        captured["hook"](user_state, bucket)
        assert seen["state"] is user_state
        assert seen["bucket"] is bucket
        assert seen["bypassed"] is True
    finally:
        unpatch()


def test_register_comm_hook_disabled_via_config(monkeypatch):
    # `config._add` only seeds defaults once; toggling the env var after
    # initial registration won't propagate. Mutate the live config attribute
    # directly to simulate `DD_PYTORCH_GRAD_COMM=false`.
    from ddtrace import config

    monkeypatch.setattr(config.pytorch, "grad_comm_enabled", False)
    fake_register = mock.Mock()
    monkeypatch.setattr(torch.nn.parallel.DistributedDataParallel, "register_comm_hook", fake_register)
    patch()
    try:
        ddp = torch.nn.parallel.DistributedDataParallel.__new__(torch.nn.parallel.DistributedDataParallel)

        def user_hook(s, b):
            return None

        ddp.register_comm_hook({}, user_hook)
        # When disabled, our wrapper passes through unmodified.
        assert fake_register.call_count == 1
        passed_hook = fake_register.call_args[0][1]
        assert passed_hook is user_hook
        assert not getattr(passed_hook, "_dd_chained", False)
    finally:
        unpatch()


def test_lazy_comm_hook_registers_once_on_pre_backward(monkeypatch):
    cls = torch.nn.parallel.DistributedDataParallel
    if not hasattr(cls, "_pre_backward_hook"):
        import pytest

        pytest.skip("PyTorch 2.0 has no _pre_backward_hook")
    register_calls = []

    def fake_register(self, state, hook):
        register_calls.append((state, hook))

    monkeypatch.setattr(cls, "register_comm_hook", fake_register)

    def fake_pre(self, *a, **k):
        return None

    monkeypatch.setattr(cls, "_pre_backward_hook", fake_pre, raising=False)
    patch()
    try:
        ddp = cls.__new__(cls)
        ddp._pre_backward_hook()
        ddp._pre_backward_hook()  # second call must not re-register
        assert len(register_calls) == 1
    finally:
        unpatch()


def test_lazy_comm_hook_skipped_when_pre_backward_absent(monkeypatch, caplog):
    cls = torch.nn.parallel.DistributedDataParallel
    if hasattr(cls, "_pre_backward_hook"):
        monkeypatch.delattr(cls, "_pre_backward_hook", raising=True)
    with caplog.at_level("INFO"):
        patch()
        try:
            assert not hasattr(cls, "_pre_backward_hook")
            assert any("_pre_backward_hook" in rec.message for rec in caplog.records)
        finally:
            unpatch()
