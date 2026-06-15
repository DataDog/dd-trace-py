"""Optimizer.__init__ wrapping is gated on Layer Two (DD_PYTORCH_PROFILING)."""

import torch
import wrapt

import ddtrace.contrib.internal.pytorch._distributed as _distributed


def test_no_step_wrap_when_both_modes_disabled(monkeypatch):
    """Step wrap is gated on (profiling OR summary). With both off, no wrap.

    The gate reads `config.pytorch.summary_profiling` (set at import time
    from `DD_PYTORCH_SUMMARY_PROFILING`), so we monkey-patch the config
    attribute directly rather than setting env after import.
    """
    from ddtrace import config

    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    monkeypatch.setattr(config.pytorch, "summary_profiling", False, raising=False)
    _distributed._install_optimizer()
    try:
        opt = torch.optim.SGD([torch.nn.Parameter(torch.randn(2))], lr=0.01)
        assert not isinstance(opt.step, wrapt.FunctionWrapper)
    finally:
        _distributed._uninstall_optimizer()


def test_step_wrap_present_when_l2_enabled(monkeypatch):
    from ddtrace import config

    monkeypatch.setenv("DD_PYTORCH_PROFILING", "true")
    monkeypatch.setattr(config.pytorch, "summary_profiling", False, raising=False)
    _distributed._install_optimizer()
    try:
        opt = torch.optim.SGD([torch.nn.Parameter(torch.randn(2))], lr=0.01)
        assert isinstance(opt.step, wrapt.FunctionWrapper)
    finally:
        _distributed._uninstall_optimizer()


def test_step_wrap_present_when_summary_enabled(monkeypatch):
    """Default summary mode (profiling=false, summary=true) still attaches
    the per-instance step wrap so the LR + optim_step_ms feeds run.
    """
    from ddtrace import config

    monkeypatch.delenv("DD_PYTORCH_PROFILING", raising=False)
    monkeypatch.setattr(config.pytorch, "summary_profiling", True, raising=False)
    _distributed._install_optimizer()
    try:
        opt = torch.optim.SGD([torch.nn.Parameter(torch.randn(2))], lr=0.01)
        assert isinstance(opt.step, wrapt.FunctionWrapper)
    finally:
        _distributed._uninstall_optimizer()
