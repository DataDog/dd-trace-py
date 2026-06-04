import torch
import torch.nn
from torch.profiler import ProfilerActivity


class TinyModel(torch.nn.Module):
    """A tiny model whose forward pass produces a nested CPU operator tree.

    A ``torch.nn.Linear`` forward records a parent ``aten::linear`` operator
    containing child operators (``aten::addmm``/``aten::matmul``/``aten::t``),
    which is what lets us assert that the profiler reconstructs nesting.
    """

    def __init__(self) -> None:
        super().__init__()
        self.net = torch.nn.Sequential(
            torch.nn.Linear(64, 64),
            torch.nn.ReLU(),
            torch.nn.Linear(64, 16),
        )

    def forward(self, x: torch.Tensor) -> torch.Tensor:
        return self.net(x)


def run() -> None:
    model = TinyModel()
    inputs = torch.randn(32, 64)

    with torch.profiler.profile(
        activities=[ProfilerActivity.CPU],
    ):
        for _ in range(5):
            model(inputs).sum().backward()


if __name__ == "__main__":
    run()
