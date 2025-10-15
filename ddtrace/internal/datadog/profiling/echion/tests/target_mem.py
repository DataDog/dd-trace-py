# This file is part of "echion" which is released under MIT.
#
# Copyright (c) 2023 Gabriele N. Tornetta <phoenix1987@gmail.com>.


from dataclasses import dataclass


a = []


@dataclass
class Foo:
    n: int


def leak():
    global a

    for i in range(1_000):
        a.append(Foo(i))


if __name__ == "__main__":
    leak()
