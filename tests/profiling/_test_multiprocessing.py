import multiprocessing
import sys
import time


def f():
    time.sleep(1)


if __name__ == "__main__":
    multiprocessing.set_start_method(sys.argv[1])

    p = multiprocessing.Process(target=f)
    p.start()
    print(p.pid)
    p.join()
