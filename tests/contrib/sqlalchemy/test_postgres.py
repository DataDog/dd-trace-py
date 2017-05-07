import psycopg2

from unittest import TestCase
from nose.tools import eq_, ok_, assert_raises

from sqlalchemy.exc import ProgrammingError

from .mixins import SQLAlchemyTestMixin
from ..config import POSTGRES_CONFIG


class PostgresTestCase(SQLAlchemyTestMixin, TestCase):
    """TestCase for Postgres Engine"""
    VENDOR = 'postgres'
    SQL_DB = 'postgres'
    SERVICE = 'postgres'
    ENGINE_ARGS = {'url': 'postgresql://%(user)s:%(password)s@%(host)s:%(port)s/%(dbname)s' % POSTGRES_CONFIG}

    def check_meta(self, span):
        # check database connection tags
        eq_(span.get_tag('out.host'), POSTGRES_CONFIG['host'])
        eq_(span.get_tag('out.port'), str(POSTGRES_CONFIG['port']))

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
        ok_('relation "a_wrong_table" does not exist' in span.get_tag('error.msg'))
        ok_('ProgrammingError' in span.get_tag('error.type'))
        ok_('ProgrammingError: relation "a_wrong_table" does not exist' in span.get_tag('error.stack'))


class PostgresCreatorTestCase(PostgresTestCase):
    """TestCase for Postgres Engine that includes the same tests set
    of `PostgresTestCase`, but it uses a specific `creator` function.
    """
    VENDOR = 'postgres'
    SQL_DB = 'postgres'
    SERVICE = 'postgres'
    ENGINE_ARGS = {'url': 'postgresql://', 'creator': lambda: psycopg2.connect(**POSTGRES_CONFIG)}
