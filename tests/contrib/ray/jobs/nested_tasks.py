import ray


ray.init()


@ray.remote
def add_one(x):
    return x + 1


@ray.remote
def submit_addition_task(x):
    futures = [add_one.remote(x + i) for i in range(3)]
    return ray.get(futures)


def main():
    future = submit_addition_task.remote(2)

    results = ray.get(future)
    assert results == [3, 4, 5], f"Unexpected results: {results}"


if __name__ == "__main__":
    main()
    ray.shutdown()
