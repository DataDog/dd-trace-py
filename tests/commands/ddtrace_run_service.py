import os

from nose.tools import eq_

if __name__ == '__main__':
    eq_(os.environ['DATADOG_SERVICE_NAME'], 'my_test_service')
    print('Test success')
