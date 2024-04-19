import pytest
from sqlalchemy import text
from sqlalchemy.exc import OperationalError

from ddtrace.constants import ERROR_MSG
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from .mixins import SQLAlchemyTestMixin


class SQLiteTestCase(SQLAlchemyTestMixin, TracerTestCase):
    """TestCase for the SQLite engine"""

    VENDOR = "sqlite"
    SQL_DB = ":memory:"
    SERVICE = "sqlite"
    ENGINE_ARGS = {"url": "sqlite:///:memory:"}

    def setUp(self):
        super(SQLiteTestCase, self).setUp()

    def tearDown(self):
        super(SQLiteTestCase, self).tearDown()

    def test_engine_execute_errors(self):
        # ensures that SQL errors are reported
        with pytest.raises(OperationalError):
            with self.connection() as conn:
                conn.execute(text("SELECT * FROM a_wrong_table")).fetchall()

        traces = self.pop_traces()
        # trace composition
        self.assertEqual(len(traces), 1)
        self.assertEqual(len(traces[0]), 1)
        span = traces[0][0]
        # span fields
        assert_is_measured(span)
        self.assertEqual(span.name, "{}.query".format(self.VENDOR))
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "SELECT * FROM a_wrong_table")
        self.assertEqual(span.get_tag("sql.db"), self.SQL_DB)
        self.assertIsNone(span.get_metric("db.row_count"))
        self.assertEqual(span.get_tag("component"), "sqlalchemy")
        self.assertEqual(span.get_tag("span.kind"), "client")
        self.assertEqual(span.span_type, "sql")
        self.assertTrue(span.duration > 0)
        # check the error
        self.assertEqual(span.error, 1)
        self.assertEqual(span.get_tag(ERROR_MSG), "no such table: a_wrong_table")
        self.assertTrue("OperationalError" in span.get_tag("error.type"))
        self.assertTrue("OperationalError: no such table: a_wrong_table" in span.get_tag("error.stack"))
