import ddtrace.profiling.auto
import torch
import torch.nn
import torch.optim
import torch.utils.data
import torchvision.datasets
import torchvision.models
import torchvision.transforms as T
from torchvision.models import resnet18, ResNet18_Weights

from torch.profiler import ProfilerActivity


def cifar():
    transform = T.Compose([T.Resize(224), T.ToTensor(), T.Normalize((0.5, 0.5, 0.5), (0.5, 0.5, 0.5))])
    train_set = torchvision.datasets.CIFAR10(root="./data", train=True, download=True, transform=transform)
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
