from __future__ import print_function

from nose.tools import eq_
import sys

if __name__ == '__main__':
    eq_(sys.argv[1:], ['foo', 'bar'])
    print('Test success')
