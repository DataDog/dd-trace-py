# We expect Echion to segfault on this test if code objects are not accessed
# in a safe manner.
#
# Program terminated with signal SIGSEGV, Segmentation fault.
# #0  0x00000000005af3c8 in PyUnicode_AsUTF8AndSize ()

from threading import Thread
from time import time

from bytecode import Bytecode


def foo(n):
    if n > 0:
        return foo(n - 1)


def replace_bytecode():
    while True:
        foo.__code__ = Bytecode.from_code(foo.__code__).to_code()


if __name__ == "__main__":
    Thread(target=replace_bytecode, daemon=True).start()

    end = time() + 5

    while time() < end:
        foo(12)
