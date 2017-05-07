from unittest import TestCase
from nose.tools import eq_, ok_, assert_raises

from sqlalchemy.exc import OperationalError

from .mixins import SQLAlchemyTestMixin


class SQLiteTestCase(SQLAlchemyTestMixin, TestCase):
    """TestCase for the SQLite engine"""
    VENDOR = 'sqlite'
    SQL_DB = ':memory:'
    SERVICE = 'sqlite'
    ENGINE_ARGS = {'url': 'sqlite:///:memory:'}

    def test_engine_execute_errors(self):
        # ensures that SQL errors are reported
        with assert_raises(OperationalError):
            with self.connection() as conn:
                conn.execute('SELECT * FROM a_wrong_table').fetchall()

        traces = self.tracer.writer.pop_traces()
        # trace composition
        eq_(len(traces), 1)
        eq_(len(traces[0]), 1)
        span = traces[0][0]
        # span fields
        eq_(span.name, '{}.query'.format(self.VENDOR))
        eq_(span.service, self.SERVICE)
        eq_(span.resource, 'SELECT * FROM a_wrong_table')
        eq_(span.get_tag('sql.db'), self.SQL_DB)
        ok_(span.get_tag('sql.rows') is None)
        eq_(span.span_type, 'sql')
        ok_(span.duration > 0)
        # check the error
        eq_(span.error, 1)
        eq_(span.get_tag('error.msg'), 'no such table: a_wrong_table')
        ok_('OperationalError' in span.get_tag('error.type'))
        ok_('OperationalError: no such table: a_wrong_table' in span.get_tag('error.stack'))
