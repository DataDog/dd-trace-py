import ray


ray.init()


@ray.remote
def add_one(x):
    return x + 1


def main():
    results = ray.get([add_one.remote(x) for x in range(4)])
    assert results == [1, 2, 3, 4], f"Expected [1, 2, 3, 4], got {results}"


if __name__ == "__main__":
    main()
    ray.shutdown()
