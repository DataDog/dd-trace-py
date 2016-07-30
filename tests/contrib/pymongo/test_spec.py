"""
tests for parsing specs.
"""

from bson.son import SON
from nose.tools import eq_

from ddtrace.contrib.pymongo.parse import parse_spec

def test_create():
    cmd = parse_spec(SON([("create", "foo")]))
    eq_(cmd.name, "create")
    eq_(cmd.coll, "foo")
    eq_(cmd.tags, {})
    eq_(cmd.metrics ,{})

def test_insert():
    spec = SON([
        ('insert', 'bla'),
        ('ordered', True),
        ('documents', ['a', 'b']),
    ])
    cmd = parse_spec(spec)
    eq_(cmd.name, "insert")
    eq_(cmd.coll, "bla")
    eq_(cmd.tags, {'mongodb.ordered':True})
    eq_(cmd.metrics, {'mongodb.documents':2})

