import torch
import torch.nn
import torch.optim
from torch.profiler import ProfilerActivity
import torch.utils.data
from torchvision.models import ResNet18_Weights
from torchvision.models import resnet18


class FakeImageDataset(torch.utils.data.Dataset):
    """Synthetic dataset that mimics CIFAR10-like data without network access."""

    def __init__(self, size: int = 320, num_classes: int = 10):
        self.size = size
        self.num_classes = num_classes

        # Pre-generate random data to avoid generating it on each __getitem__
        self.images = torch.randn(size, 3, 224, 224)
        self.labels = torch.randint(0, num_classes, (size,))

    def __len__(self) -> int:
        return self.size

    def __getitem__(self, idx: int) -> tuple:
        return self.images[idx], self.labels[idx]


def cifar():
    train_set = FakeImageDataset(size=320)
    train_loader = torch.utils.data.DataLoader(train_set, batch_size=32, shuffle=True)
    device = torch.device("cuda")
    model = resnet18(weights=ResNet18_Weights.DEFAULT).cuda()
    criterion = torch.nn.CrossEntropyLoss()
    optimizer = torch.optim.SGD(model.parameters(), lr=0.001, momentum=0.9)
    model.train()

    def train(data):
        inputs, labels = data[0].to(device=device), data[1].to(device=device)
        outputs = model(inputs)
        loss = criterion(outputs, labels)
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

    with torch.profiler.profile(
        activities=[ProfilerActivity.CUDA],
    ):
        for step, batch_data in enumerate(train_loader):
            print("step #%d" % step)
            if step >= (1 + 1 + 3) * 2:
                break
            train(batch_data)


if __name__ == "__main__":
    cifar()
