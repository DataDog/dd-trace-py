import ray


ray.init()


@ray.remote
def add_one(x):
    return x + 1


def main():
    done, running = ray.wait([add_one.remote(42)], num_returns=1, timeout=60)
    assert running == [], f"Expected no running tasks, got {len(running)}"
    assert ray.get(done) == [43], f"Expected done to be [43], got {done}"


if __name__ == "__main__":
    main()
    ray.shutdown()
