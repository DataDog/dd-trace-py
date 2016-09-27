# 3d party
from nose.tools import eq_, ok_
from django.test import TestCase

# project
from ddtrace.contrib.django.utils import quantize_key_values


class DjangoUtilsTest(TestCase):
    def test_quantize_key_values(self):
        """
        Ensure that the utility functions properly convert a dictionary object
        """
        key = {'second_key': 2, 'first_key': 1}
        result = quantize_key_values(key)
        eq_(len(result), 2)
        ok_('first_key' in result)
        ok_('second_key' in result)
