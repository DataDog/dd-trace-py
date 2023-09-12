"""
CAVEAT: the line number is important to some IAST tests, be careful to modify this file and update the tests if you
make some changes
"""
import os


ROOT_DIR = os.path.dirname(os.path.abspath(__file__))


def pt_open(origin_string):
    m = open(origin_string)
    return m.read()
