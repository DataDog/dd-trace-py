#!/usr/bin/env python3


def read_from_io(my_object):
    try:
        my_object.read(5)
    except AttributeError as e:
        raise AttributeError("Object does not have a read method") from e
