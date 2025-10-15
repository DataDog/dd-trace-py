from time import sleep
from time import monotonic as time
from threading import Thread


def cpu_sleep(t):
    end = time() + t
    while time() <= end:
        pass


def foo():
    cpu_sleep(1)


def bar():
    sleep(2)
    foo()


def main():
    bar()


if __name__ == "__main__":
    t = Thread(target=main, name="SecondaryThread")
    t.start()

    main()

    t.join()
