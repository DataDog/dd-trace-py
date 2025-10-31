import ray


ray.init(logging_level=0)


@ray.remote
def compute_value(x):
    return x + 1


@ray.remote
def batch_compute(values):
    futures = [compute_value.remote(val) for val in values]
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

        return {"computation_count": self.get_count(), "results": set(results), "total_stored": len(self.get_results())}


def main():
    manager = ComputationManager.remote()
    result = ray.get(manager.compute_and_store.remote([2, 3, 4]))
    assert result == {
        "computation_count": 1,
        "results": {3, 4, 5},
        "total_stored": 3,
    }, f"Unexpected results: {result['results']}"


if __name__ == "__main__":
    main()
    ray.shutdown()
