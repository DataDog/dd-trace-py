import ray


ray.init()


@ray.remote
def add_one(x):
    return x + 1


def main():
    answer = 42
    object_ref = ray.put(answer)
    futures = [add_one.remote(object_ref)]
    results = ray.get(futures)
    assert results == [43], f"Unexpected results: {results}"


if __name__ == "__main__":
    main()
    ray.shutdown()
