import psycopg2
import pytest
from sqlalchemy.exc import ProgrammingError

from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from ..config import POSTGRES_CONFIG
from .mixins import SQLAlchemyTestMixin


class PostgresTestCase(SQLAlchemyTestMixin, TracerTestCase):
    """TestCase for Postgres Engine"""
    VENDOR = 'postgres'
    SQL_DB = 'postgres'
    SERVICE = 'postgres'
    ENGINE_ARGS = {'url': 'postgresql://%(user)s:%(password)s@%(host)s:%(port)s/%(dbname)s' % POSTGRES_CONFIG}

    def setUp(self):
        super(PostgresTestCase, self).setUp()

    def tearDown(self):
        super(PostgresTestCase, self).tearDown()

    def check_meta(self, span):
        # check database connection tags
        self.assertEqual(span.get_tag('out.host'), POSTGRES_CONFIG['host'])
        self.assertEqual(span.get_metric('out.port'), POSTGRES_CONFIG['port'])

    def test_engine_execute_errors(self):
        # ensures that SQL errors are reported
        with pytest.raises(ProgrammingError):
            with self.connection() as conn:
                conn.execute('SELECT * FROM a_wrong_table').fetchall()

        traces = self.pop_traces()
        # trace composition
        self.assertEqual(len(traces), 1)
        self.assertEqual(len(traces[0]), 1)
        span = traces[0][0]
        # span fields
        assert_is_measured(span)
        self.assertEqual(span.name, '{}.query'.format(self.VENDOR))
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, 'SELECT * FROM a_wrong_table')
        self.assertEqual(span.get_tag('sql.db'), self.SQL_DB)
        self.assertIsNone(span.get_tag('sql.rows') or span.get_metric('sql.rows'))
        self.check_meta(span)
        self.assertEqual(span.span_type, 'sql')
        self.assertTrue(span.duration > 0)
        # check the error
        self.assertEqual(span.error, 1)
        self.assertTrue('relation "a_wrong_table" does not exist' in span.get_tag('error.msg'))
        assert 'psycopg2.errors.UndefinedTable' in span.get_tag('error.type')
        assert 'UndefinedTable: relation "a_wrong_table" does not exist' in span.get_tag('error.stack')


class PostgresCreatorTestCase(PostgresTestCase):
    """TestCase for Postgres Engine that includes the same tests set
    of `PostgresTestCase`, but it uses a specific `creator` function.
    """
    VENDOR = 'postgres'
    SQL_DB = 'postgres'
    SERVICE = 'postgres'
    ENGINE_ARGS = {'url': 'postgresql://', 'creator': lambda: psycopg2.connect(**POSTGRES_CONFIG)}
