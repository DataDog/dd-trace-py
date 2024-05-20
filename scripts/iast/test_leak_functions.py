import ddtrace.auto  # noqa: F401  # isort: skip
import resource
import sys

from mod_leak_functions import test_doit


def test_main():
    try:
        rounds = int(sys.argv[1])
    except ValueError:
        rounds = 1

    for i in range(rounds):
        try:
            test_doit()
        except KeyboardInterrupt:
            print("Control-C stopped at %d rounds" % i)
            break
        if i % 10000 == 0:
            print("Round %d Max RSS: " % i, end="")
            print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)
    print(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024)


if __name__ == "__main__":
    test_main()
