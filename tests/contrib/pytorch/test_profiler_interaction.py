"""
Verify our pytorch integration doesn't interfere with torch.profiler.profile().
Tests:
  1. profiler runs and captures ops while integration is active
  2. profiler schedule + step_num callback fires correctly
  3. profiler works normally after unpatch
"""

import pytest
import torch
import torch.nn as nn
import torch.optim as optim

import ddtrace


@pytest.fixture(autouse=True)
def _patch_pytorch():
    ddtrace.patch(pytorch=True)
    yield
    ddtrace.patch(pytorch=False)


@pytest.fixture()
def _simple_model():
    model = nn.Linear(10, 5)
    optimizer = optim.SGD(model.parameters(), lr=0.01)
    x = torch.randn(4, 10)
    y = torch.randn(4, 5)
    return model, optimizer, x, y


def test_profiler_captures_ops_while_integration_active(_simple_model):
    model, optimizer, x, y = _simple_model
    with torch.profiler.profile(
        activities=[torch.profiler.ProfilerActivity.CPU],
        record_shapes=True,
    ) as prof:
        out = model(x)
        loss = nn.functional.mse_loss(out, y)
        loss.backward()
        optimizer.step()
        optimizer.zero_grad()

    events = prof.key_averages()
    assert len(events) > 0, "profiler captured no events while integration was active"


def test_profiler_schedule_fires_while_integration_active(_simple_model):
    model, optimizer, x, y = _simple_model
    steps_seen = []

    def trace_handler(p):
        steps_seen.append(p.step_num)

    with torch.profiler.profile(
        activities=[torch.profiler.ProfilerActivity.CPU],
        schedule=torch.profiler.schedule(wait=0, warmup=0, active=2),
        on_trace_ready=trace_handler,
    ) as prof:
        for _ in range(4):
            out = model(x)
            loss = nn.functional.mse_loss(out, y)
            loss.backward()
            optimizer.step()
            optimizer.zero_grad()
            prof.step()

    assert len(steps_seen) > 0, "on_trace_ready never called while integration was active"


def test_profiler_works_after_unpatch(_simple_model):
    model, optimizer, x, y = _simple_model
    ddtrace.patch(pytorch=False)  # unpatch early (fixture will also unpatch — idempotent)

    with torch.profiler.profile(activities=[torch.profiler.ProfilerActivity.CPU]) as prof:
        out = model(x)
        loss = nn.functional.mse_loss(out, y)
        loss.backward()
        optimizer.step()

    events = prof.key_averages()
    assert len(events) > 0, "profiler captured no events after unpatch"
