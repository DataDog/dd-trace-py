"""Small PyTorch training loop, profiled with ddtrace."""

import torch
import torch.nn as nn
import torch.optim as optim
from torch.utils.data import DataLoader, TensorDataset

from ddtrace.profiling import Profiler


def build_model() -> nn.Sequential:
    return nn.Sequential(
        nn.Linear(784, 256),
        nn.ReLU(),
        nn.Linear(256, 128),
        nn.ReLU(),
        nn.Linear(128, 10),
    )


def make_fake_data(n: int = 10_000) -> TensorDataset:
    X = torch.randn(n, 784)
    y = torch.randint(0, 10, (n,))
    return TensorDataset(X, y)


def train(model: nn.Module, loader: DataLoader, epochs: int = 5) -> None:
    criterion = nn.CrossEntropyLoss()
    optimizer = optim.Adam(model.parameters(), lr=1e-3)

    for epoch in range(epochs):
        total_loss = 0.0
        for batch_X, batch_y in loader:
            optimizer.zero_grad()
            out = model(batch_X)
            loss = criterion(out, batch_y)
            loss.backward()
            optimizer.step()
            total_loss += loss.item()
        print(f"epoch {epoch + 1}/{epochs}  loss={total_loss / len(loader):.4f}")


def main() -> None:
    prof = Profiler()
    prof.start()

    model = build_model()
    dataset = make_fake_data()
    loader = DataLoader(dataset, batch_size=64, shuffle=True)
    train(model, loader, epochs=10)

    prof.stop()


if __name__ == "__main__":
    main()
