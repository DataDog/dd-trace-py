import ray


@ray.remote
def add_one(x):
    return x + 1


def main():
    futures_add_one = add_one.remote(0)
    result = ray.get(futures_add_one)
    assert result == 1, f"Unexpected results: {result}"


if __name__ == "__main__":
    main()
