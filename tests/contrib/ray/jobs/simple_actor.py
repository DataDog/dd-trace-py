import ray


ray.init()


@ray.remote
class Counter:
    def __init__(self):
        self.value = 0

    def increment(self):
        self.value += 1
        return self.value

    def get_value(self):
        return self.value

    def increment_and_get(self):
        self.increment()
        return self.get_value()

    def increment_get_and_double(self):
        value = self.increment_and_get()
        return value * 2


def main():
    counter_actor = Counter.remote()
    current_value = ray.get(counter_actor.increment_get_and_double.remote())

    assert current_value == 2, f"Unexpected result: {current_value}"


if __name__ == "__main__":
    main()
    ray.shutdown()
