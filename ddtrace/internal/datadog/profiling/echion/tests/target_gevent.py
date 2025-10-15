import gevent
import gevent.monkey


gevent.monkey.patch_all()


def f1():
    print("f1")
    gevent.sleep(1)
    print("f1 done")


def f2():
    print("f2")
    gevent.sleep(2)
    print("f2 done")


def f3():
    print("f3")
    gevent.sleep(1)
    gevent.spawn(f1).join()
    print("f3 done")


def main():
    gevent.joinall([gevent.spawn(f3), gevent.spawn(f2)])


if __name__ == "__main__":
    main()
