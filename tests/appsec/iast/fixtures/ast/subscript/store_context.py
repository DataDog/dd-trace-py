#!/usr/bin/env python3


def fixture_subscript_store_context():
    my_list = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]

    for i in range(10):
        for my_list[i] in range(10):
            yield my_list
