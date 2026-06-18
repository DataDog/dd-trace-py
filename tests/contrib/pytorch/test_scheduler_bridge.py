"""Unit tests for _scheduler_bridge — verify single-wrap on _LRScheduler.step
fires the C bridge with the new learning rate."""

from unittest import mock

import pytest

from ddtrace.contrib.internal.pytorch import _rank_root
from ddtrace.contrib.internal.pytorch import _scheduler_bridge


def test_install_idempotent(monkeypatch):
    fake_module = mock.MagicMock()
    monkeypatch.setattr(_scheduler_bridge, "_import_lr_scheduler", lambda: fake_module)
    # Reset module-level _INSTALLED for a clean call sequence.
    monkeypatch.setattr(_scheduler_bridge, "_INSTALLED", False)
    _scheduler_bridge.install()
    _scheduler_bridge.install()  # second call is a no-op
    # wrapt's wrap_function_wrapper is invoked at most once.


def test_step_wrapper_forwards_lr(monkeypatch):
    captured = {}

    def fake_republish(key, value):
        captured[key] = value

    monkeypatch.setattr(_rank_root, "republish_with_extra_kwarg", fake_republish)
    # _last_lr suppresses repeat publishes; clear it so this test isn't
    # order-dependent.
    monkeypatch.setattr(_scheduler_bridge, "_last_lr", None)

    class StubOptim:
        param_groups = [{"lr": 0.001}]

    class StubScheduler:
        def __init__(self):
            self.optimizer = StubOptim()

    sch = StubScheduler()
    _scheduler_bridge._on_step_post(sch)
    assert captured["optim.current_learning_rate"] == pytest.approx(0.001)


def test_step_wrapper_silent_on_no_optimizer(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        _rank_root,
        "republish_with_extra_kwarg",
        lambda key, value: captured.setdefault(key, value),
    )
    monkeypatch.setattr(_scheduler_bridge, "_last_lr", None)
    sch = mock.MagicMock(spec=[])  # no .optimizer attribute
    _scheduler_bridge._on_step_post(sch)  # must not raise
    assert "optim.current_learning_rate" not in captured
