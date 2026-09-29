import random
import threading
import time


lock: threading.Lock = threading.Lock()
shared: list[bytes] = []


def cpu_work() -> int:
    total: int = 0
    for i in range(200_000):
        total += i * i % 7
    return total


def memory_work() -> None:
    chunks: list[bytes] = [bytes(random.randint(1_000, 100_000)) for _ in range(50)]
    with lock:
        shared.extend(chunks)
        if len(shared) > 500:
            del shared[:250]


def exception_work() -> None:
    for _ in range(100):
        try:
            raise ValueError("boom")
        except ValueError:
            pass


def lock_work() -> None:
    with lock:
        time.sleep(0.01)


def worker() -> None:
    while True:
        cpu_work()
        memory_work()
        exception_work()
        lock_work()


def main() -> None:
    threads: list[threading.Thread] = [threading.Thread(target=worker, daemon=True) for _ in range(4)]
    for thread in threads:
        thread.start()
    while True:
        time.sleep(1)


if __name__ == "__main__":
    main()
