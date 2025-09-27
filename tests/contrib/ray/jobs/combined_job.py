import ray


ray.init()


# Common remote function from simple_task, nested_tasks, simple_wait
@ray.remote
def add_one(x):
    return x + 1


# From nested_tasks
@ray.remote
def submit_addition_task(x):
    futures = [add_one.remote(x + i) for i in range(3)]
    return ray.get(futures)


# From simple_actor
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


# From actor_and_task
# compute_value is the same as add_one, so we reuse add_one
@ray.remote
def batch_compute(values):
    futures = [add_one.remote(val) for val in values]
    return ray.get(futures)


@ray.remote
class ComputationManager:
    def __init__(self):
        self.computation_count = 0
        self.results = []

    def increment_count(self):
        self.computation_count += 1
        return self.computation_count

    def get_count(self):
        return self.computation_count

    def add_result(self, result):
        self.results.append(result)
        return len(self.results)

    def get_results(self):
        return self.results

    def compute_and_store(self, values):
        self.increment_count()

        future = batch_compute.remote(values)
        results = ray.get(future)

        for result in results:
            self.add_result(result)

        return {
            "computation_count": self.get_count(),
            "results": set(results),
            "total_stored": len(self.get_results()),
        }


# From actor_interactions
@ray.remote
class Sender:
    def __init__(self):
        self.sent_count = 0

    def send_message(self, receiver, message):
        self.sent_count += 1
        future = receiver.receive_message.remote(message)
        return ray.get(future)

    def get_sent_count(self):
        return self.sent_count


@ray.remote
class Receiver:
    def __init__(self):
        self.received_messages = []

    def receive_message(self, message):
        self.received_messages.append(message)
        return f"received: {message}"

    def get_messages(self):
        return self.received_messages


def main():
    print("Running main_simple_task")
    futures = [add_one.remote(i) for i in range(4)]
    results = ray.get(futures)
    assert results == [1, 2, 3, 4], f"Unexpected results in simple_task: {results}"
    print("Finished main_simple_task")

    print("Running main_nested_tasks")
    future = submit_addition_task.remote(2)
    results = ray.get(future)
    assert results == [3, 4, 5], f"Unexpected results in nested_tasks: {results}"
    print("Finished main_nested_tasks")

    print("Running main_simple_actor")
    counter_actor = Counter.remote()
    current_value = ray.get(counter_actor.increment_get_and_double.remote())
    assert current_value == 2, f"Unexpected result in simple_actor: {current_value}"
    print("Finished main_simple_actor")

    print("Running main_actor_and_task")
    manager = ComputationManager.remote()
    result = ray.get(manager.compute_and_store.remote([2, 3, 4]))
    assert result == {
        "computation_count": 1,
        "results": {3, 4, 5},
        "total_stored": 3,
    }, f"Unexpected results in actor_and_task: {result['results']}"
    print("Finished main_actor_and_task")

    print("Running main_actor_interactions")
    sender = Sender.remote()
    receiver = Receiver.remote()
    result = ray.get(sender.send_message.remote(receiver, "hello"))
    sent_count = ray.get(sender.get_sent_count.remote())
    messages = ray.get(receiver.get_messages.remote())
    assert result == "received: hello"
    assert sent_count == 1
    assert messages == ["hello"]
    print("Finished main_actor_interactions")

    print("Running main_simple_wait")
    done, running = ray.wait([add_one.remote(42)], num_returns=1, timeout=60)
    assert running == [], f"Expected no running tasks in simple_wait, got {len(running)}"
    assert ray.get(done) == [43], f"Expected done to be [43] in simple_wait, got {ray.get(done)}"
    print("Finished main_simple_wait")


if __name__ == "__main__":
    main()
    ray.shutdown()
