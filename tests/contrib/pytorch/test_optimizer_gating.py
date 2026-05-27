"""Optimizer.__init__ wrapping is gated on Layer Two (DD_PYTORCH_PROFILING)."""

import torch
import wrapt

import ddtrace.contrib.internal.pytorch._distributed as _distributed


def test_no_step_wrap_when_l2_disabled(monkeypatch):
    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    _distributed._install_optimizer()
    try:
        opt = torch.optim.SGD([torch.nn.Parameter(torch.randn(2))], lr=0.01)
        assert not isinstance(opt.step, wrapt.FunctionWrapper)
    finally:
        _distributed._uninstall_optimizer()


def test_step_wrap_present_when_l2_enabled(monkeypatch):
    monkeypatch.setenv("DD_PYTORCH_PROFILING", "true")
    _distributed._install_optimizer()
    try:
        opt = torch.optim.SGD([torch.nn.Parameter(torch.randn(2))], lr=0.01)
        assert isinstance(opt.step, wrapt.FunctionWrapper)
    finally:
        _distributed._uninstall_optimizer()
