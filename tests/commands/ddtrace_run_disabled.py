from __future__ import print_function

from ddtrace import tracer, monkey

from nose.tools import ok_, eq_

if __name__ == '__main__':
    ok_(not tracer.enabled)
    eq_(len(monkey.get_patched_modules()), 0)
    print("Test success")
