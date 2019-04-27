"""
tests for parsing specs.
"""

from bson.son import SON
from nose.tools import eq_

from ddtrace.contrib.pymongo.parse import parse_spec


def test_empty():
    cmd = parse_spec(SON([]))
    assert cmd is None


def test_create():
    cmd = parse_spec(SON([('create', 'foo')]))
    eq_(cmd.name, 'create')
    eq_(cmd.coll, 'foo')
    eq_(cmd.tags, {})
    eq_(cmd.metrics, {})


def test_insert():
    spec = SON([
        ('insert', 'bla'),
        ('ordered', True),
        ('documents', ['a', 'b']),
    ])
    cmd = parse_spec(spec)
    eq_(cmd.name, 'insert')
    eq_(cmd.coll, 'bla')
    eq_(cmd.tags, {'mongodb.ordered': True})
    eq_(cmd.metrics, {'mongodb.documents': 2})


def test_update():
    spec = SON([
        ('update', u'songs'),
        ('ordered', True),
        ('updates', [
            SON([
                ('q', {'artist': 'Neil'}),
                ('u', {'$set': {'artist': 'Shakey'}}),
                ('multi', True),
                ('upsert', False)
            ])
        ])
    ])
    cmd = parse_spec(spec)
    eq_(cmd.name, 'update')
    eq_(cmd.coll, 'songs')
    eq_(cmd.query, {'artist': 'Neil'})
