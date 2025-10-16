from time import monotonic as time


def sleep(t):
    end = time() + t
    while time() <= end:
        pass


def foo():
    sleep(0.4)


def bar():
    sleep(0.8)
    foo()


def main():
    while True:
        bar()


if __name__ == "__main__":
    import os

    print(os.getpid())
    main()
