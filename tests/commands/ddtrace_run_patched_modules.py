from __future__ import print_function

from ddtrace import monkey

from nose.tools import ok_

if __name__ == '__main__':
    ok_('redis' in monkey.get_patched_modules())
    print("Test success")
