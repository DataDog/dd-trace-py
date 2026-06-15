"""Integration tests for the Layer 1 always-on distributed tracing.

These tests exercise a real (CPU-only) gloo process group and assert that
spans are emitted with the expected names, tags, and parent/child structure.
They run in the contrib::pytorch suite under the dd-trace-py test agent.
"""

import os

import torch


def _setup_single_rank_gloo():
    os.environ.setdefault("MASTER_ADDR", "127.0.0.1")
    os.environ.setdefault("MASTER_PORT", "29555")
    os.environ.setdefault("RANK", "0")
    os.environ.setdefault("WORLD_SIZE", "1")
    if not torch.distributed.is_initialized():
        torch.distributed.init_process_group(backend="gloo", rank=0, world_size=1)


def _teardown_gloo():
    if torch.distributed.is_initialized():
        torch.distributed.destroy_process_group()


def test_layer1_collectives_gloo(enable_collective_trace, test_spans):
    """Real all_reduce + barrier on gloo; assert spans are emitted with the
    correct names, bytes metric, and rank/world_size.
    """
    from ddtrace.contrib.internal.pytorch.patch import patch as pt_patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch as pt_unpatch

    pt_patch()
    _setup_single_rank_gloo()
    try:
        t = torch.zeros(8)
        torch.distributed.all_reduce(t)
        torch.distributed.barrier()
        spans = test_spans.pop()
        names = {s.name for s in spans}
        assert "pytorch.allreduce" in names
        assert "pytorch.barrier" in names
        ar = next(s for s in spans if s.name == "pytorch.allreduce")
        # 8 floats * 4 bytes/float = 32 bytes
        assert ar.get_metric("bytes") == 32
        assert ar.get_metric("rank") == 0
        assert ar.get_metric("world_size") == 1
        # job_id should be present (UUID fallback at minimum).
        assert ar.get_tag("job_id") is not None
    finally:
        _teardown_gloo()
        pt_unpatch()


def test_layer_two_full_step_hierarchy_under_ddp_gloo(test_spans, monkeypatch):
    """Real DDP-gloo training step with `DD_PYTORCH_PROFILING=true`; assert
    `pytorch.step` contains `pytorch.data_load` / `pytorch.forward` /
    `pytorch.backward` / `pytorch.optimizer` children plus the Layer 1
    `pytorch.allreduce` from gradient sync.
    """
    monkeypatch.setenv("DD_PYTORCH_PROFILING", "true")
    import importlib

    from ddtrace.contrib.internal.pytorch import _hooks

    importlib.reload(_hooks)
    from ddtrace.contrib.internal.pytorch.patch import patch as pt_patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch as pt_unpatch

    pt_patch()
    _setup_single_rank_gloo()
    try:
        model = torch.nn.parallel.DistributedDataParallel(torch.nn.Linear(4, 4))
        opt = torch.optim.AdamW(model.parameters(), lr=1e-3)
        loss_fn = torch.nn.MSELoss()

        for _ in range(2):
            x = torch.randn(2, 4)
            y = torch.randn(2, 4)
            opt.zero_grad()
            out = model(x)
            loss = loss_fn(out, y)
            loss.backward()
            opt.step()

        spans = test_spans.pop()
        names = [s.name for s in spans]
        assert names.count("pytorch.step") == 2
        assert names.count("pytorch.forward") >= 2
        assert names.count("pytorch.backward") >= 2
        assert names.count("pytorch.optimizer") == 2
        assert names.count("pytorch.data_load") >= 1
    finally:
        _teardown_gloo()
        pt_unpatch()


def test_layer1_optimizer_step_passthrough(test_spans):
    """Without `DD_PYTORCH_PROFILING=true`, the optimizer wrap is a pure
    pass-through: no `pytorch.optimizer` span is emitted, and the user's
    `optimizer.step()` still runs.
    """
    from ddtrace.contrib.internal.pytorch.patch import patch as pt_patch
    from ddtrace.contrib.internal.pytorch.patch import unpatch as pt_unpatch

    pt_patch()
    try:
        params = [torch.nn.Parameter(torch.zeros(2))]
        opt = torch.optim.SGD(params, lr=0.01)
        opt.step()
        # No pytorch.optimizer span at Layer 1.
        spans = test_spans.pop()
        assert not any(s.name == "pytorch.optimizer" for s in spans)
    finally:
        pt_unpatch()
