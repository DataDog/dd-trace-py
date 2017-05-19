from unittest import TestCase
from nose.tools import eq_, ok_, assert_raises

from sqlalchemy.exc import ProgrammingError

from .mixins import SQLAlchemyTestMixin
from ..config import MYSQL_CONFIG


class MysqlConnectorTestCase(SQLAlchemyTestMixin, TestCase):
    """TestCase for mysql-connector engine"""
    VENDOR = 'mysql'
    SQL_DB = 'test'
    SERVICE = 'mysql'
    ENGINE_ARGS = {'url': 'mysql+mysqlconnector://%(user)s:%(password)s@%(host)s:%(port)s/%(database)s' % MYSQL_CONFIG}

    def check_meta(self, span):
        # check database connection tags
        eq_(span.get_tag('out.host'), MYSQL_CONFIG['host'])
        eq_(span.get_tag('out.port'), str(MYSQL_CONFIG['port']))

    def test_engine_execute_errors(self):
        # ensures that SQL errors are reported
        with assert_raises(ProgrammingError):
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
        self.check_meta(span)
        eq_(span.span_type, 'sql')
        ok_(span.duration > 0)
        # check the error
        eq_(span.error, 1)
        eq_(span.get_tag('error.type'), 'mysql.connector.errors.ProgrammingError')
        ok_("Table 'test.a_wrong_table' doesn't exist" in span.get_tag('error.msg'))
        ok_("Table 'test.a_wrong_table' doesn't exist" in span.get_tag('error.stack'))
