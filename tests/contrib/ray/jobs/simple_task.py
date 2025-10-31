import ray


ray.init()


@ray.remote
def add_one(x):
    return x + 1


def main():
    futures = [add_one.remote(i) for i in range(4)]
    results = ray.get(futures)
    assert results == [1, 2, 3, 4], f"Unexpected results: {results}"


if __name__ == "__main__":
    main()
    ray.shutdown()
