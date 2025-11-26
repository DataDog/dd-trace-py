import ray

from ddtrace.contrib.ray import enable_tracing_before_init


enable_tracing_before_init()


@ray.remote
class Counter:
    def __init__(self):
        self.value = 0

    def increment(self):
        self.value += 1
        return self.value

    def get_value(self):
        return self.value


def main():
    counter = Counter.remote()
    result = ray.get(counter.increment.remote())
    assert result == 1, f"Expected 1, got {result}"


if __name__ == "__main__":
    main()
