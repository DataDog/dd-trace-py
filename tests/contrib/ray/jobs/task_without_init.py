import ray


@ray.remote
def add_one(x):
    return x + 1


@ray.remote
def add_two(x):
    return x + 2


def main():
    futures_add_one = [add_one.remote(i) for i in range(2)]
    results = ray.get(futures_add_one)
    assert results == [1, 2], f"Unexpected results: {results}"

    futures_add_two = [add_two.remote(i) for i in range(2)]
    results = ray.get(futures_add_two)
    assert results == [2, 3], f"Unexpected results: {results}"


if __name__ == "__main__":
    main()
