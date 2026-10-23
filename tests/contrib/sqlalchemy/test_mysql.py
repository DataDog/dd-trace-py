import pytest
from sqlalchemy import text
from sqlalchemy.exc import ProgrammingError

from ddtrace.constants import ERROR_MSG
from ddtrace.internal.schema.default import DEFAULT_SPAN_SERVICE_NAME
from tests.utils import TracerTestCase
from tests.utils import assert_is_measured

from ..config import MYSQL_CONFIG
from .mixins import SQLAlchemyTestBase
from .mixins import SQLAlchemyTestMixin


class MysqlConnectorTestCase(SQLAlchemyTestMixin, TracerTestCase):
    """TestCase for mysql-connector engine"""

    VENDOR = "mysql"
    SQL_DB = "test"
    SERVICE = "mysql"
    ENGINE_ARGS = {"url": "mysql+mysqlconnector://%(user)s:%(password)s@%(host)s:%(port)s/%(database)s" % MYSQL_CONFIG}

    def setUp(self):
        super().setUp()

    def tearDown(self):
        super().tearDown()

    def check_meta(self, span):
        # check database connection tags
        self.assertEqual(span.get_tag("out.host"), MYSQL_CONFIG["host"])
        self.assertEqual(span.get_metric("network.destination.port"), MYSQL_CONFIG["port"])

    def test_engine_execute_errors(self):
        # ensures that SQL errors are reported
        with pytest.raises(ProgrammingError):
            with self.connection() as conn:
                conn.execute(text("SELECT * FROM a_wrong_table")).fetchall()

        traces = self.pop_traces()
        # trace composition
        self.assertEqual(len(traces), 1)
        self.assertEqual(len(traces[0]), 1)
        span = traces[0][0]
        # span fields
        assert_is_measured(span)
        self.assertEqual(span.name, f"{self.VENDOR}.query")
        self.assertEqual(span.service, self.SERVICE)
        self.assertEqual(span.resource, "SELECT * FROM a_wrong_table")
        self.assertEqual(span.get_tag("sql.db"), self.SQL_DB)
        self.assertIsNone(span.get_metric("db.row_count"))
        self.check_meta(span)
        self.assertEqual(span.span_type, "sql")
        self.assertTrue(span.duration > 0)
        # check the error
        self.assertEqual(span.error, 1)
        self.assertEqual(span.get_tag("error.type"), "mysql.connector.errors.ProgrammingError")
        self.assertTrue("Table 'test.a_wrong_table' doesn't exist" in span.get_tag(ERROR_MSG))
        self.assertTrue("Table 'test.a_wrong_table' doesn't exist" in span.get_tag("error.stack"))


class TestSchematization(SQLAlchemyTestBase, TracerTestCase):
    """TestCase for mysql-connector engine"""

    ENGINE_ARGS = {"url": "mysql+mysqlconnector://%(user)s:%(password)s@%(host)s:%(port)s/%(database)s" % MYSQL_CONFIG}

    def setUp(self):
        super().setUp()

    def tearDown(self):
        super().tearDown()

    def _generate_span(self):
        with pytest.raises(ProgrammingError):
            with self.connection() as conn:
                conn.execute(text("SELECT * FROM a_wrong_table")).fetchall()

        traces = self.pop_traces()
        span = traces[0][0]

        return span

    def _scenario_test_schematized_service_name_default(self):
        span = self._generate_span()

        self.assertEqual(span.service, "mysql")

    def _scenario_test_schematized_service_name_v0(self):
        span = self._generate_span()

        self.assertEqual(span.service, "mysql")

    def _scenario_test_schematized_service_name_v1(self):
        span = self._generate_span()

        self.assertEqual(span.service, "mysvc")

    def _scenario_test_schematized_unspecified_service_name_default(self):
        span = self._generate_span()

        self.assertEqual(span.service, "mysql")

    def _scenario_test_schematized_unspecified_service_name_v0(self):
        span = self._generate_span()

        self.assertEqual(span.service, "mysql")

    def _scenario_test_schematized_unspecified_service_name_v1(self):
        span = self._generate_span()

        self.assertEqual(span.service, DEFAULT_SPAN_SERVICE_NAME)

    def _scenario_test_schematized_operation_name_v0(self):
        span = self._generate_span()

        self.assertEqual(span.name, "mysql.query")

    def _scenario_test_schematized_operation_name_v1(self):
        span = self._generate_span()

        self.assertEqual(span.name, "mysql.query")

    def test_engine_connect_execute(self):
        pass


@pytest.mark.subprocess(env=dict(DD_SERVICE="mysvc"), out=None, err=None, command=True, test_assertions=True)
def test_TestSchematization_schematized_service_name_default():
    from tests.contrib.sqlalchemy.test_mysql import TestSchematization
    from tests.process import case_context

    with case_context(TestSchematization, "_scenario_test_schematized_service_name_default") as case:
        case._scenario_test_schematized_service_name_default()


@pytest.mark.subprocess(
    env=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"),
    out=None,
    err=None,
    command=True,
    test_assertions=True,
)
def test_TestSchematization_schematized_service_name_v0():
    from tests.contrib.sqlalchemy.test_mysql import TestSchematization
    from tests.process import case_context

    with case_context(TestSchematization, "_scenario_test_schematized_service_name_v0") as case:
        case._scenario_test_schematized_service_name_v0()


@pytest.mark.subprocess(
    env=dict(DD_SERVICE="mysvc", DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"),
    out=None,
    err=None,
    command=True,
    test_assertions=True,
)
def test_TestSchematization_schematized_service_name_v1():
    from tests.contrib.sqlalchemy.test_mysql import TestSchematization
    from tests.process import case_context

    with case_context(TestSchematization, "_scenario_test_schematized_service_name_v1") as case:
        case._scenario_test_schematized_service_name_v1()


@pytest.mark.subprocess(env=dict(), out=None, err=None, command=True, test_assertions=True)
def test_TestSchematization_schematized_unspecified_service_name_default():
    from tests.contrib.sqlalchemy.test_mysql import TestSchematization
    from tests.process import case_context

    with case_context(TestSchematization, "_scenario_test_schematized_unspecified_service_name_default") as case:
        case._scenario_test_schematized_unspecified_service_name_default()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"), out=None, err=None, command=True, test_assertions=True
)
def test_TestSchematization_schematized_unspecified_service_name_v0():
    from tests.contrib.sqlalchemy.test_mysql import TestSchematization
    from tests.process import case_context

    with case_context(TestSchematization, "_scenario_test_schematized_unspecified_service_name_v0") as case:
        case._scenario_test_schematized_unspecified_service_name_v0()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"), out=None, err=None, command=True, test_assertions=True
)
def test_TestSchematization_schematized_unspecified_service_name_v1():
    from tests.contrib.sqlalchemy.test_mysql import TestSchematization
    from tests.process import case_context

    with case_context(TestSchematization, "_scenario_test_schematized_unspecified_service_name_v1") as case:
        case._scenario_test_schematized_unspecified_service_name_v1()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v0"), out=None, err=None, command=True, test_assertions=True
)
def test_TestSchematization_schematized_operation_name_v0():
    from tests.contrib.sqlalchemy.test_mysql import TestSchematization
    from tests.process import case_context

    with case_context(TestSchematization, "_scenario_test_schematized_operation_name_v0") as case:
        case._scenario_test_schematized_operation_name_v0()


@pytest.mark.subprocess(
    env=dict(DD_TRACE_SPAN_ATTRIBUTE_SCHEMA="v1"), out=None, err=None, command=True, test_assertions=True
)
def test_TestSchematization_schematized_operation_name_v1():
    from tests.contrib.sqlalchemy.test_mysql import TestSchematization
    from tests.process import case_context

    with case_context(TestSchematization, "_scenario_test_schematized_operation_name_v1") as case:
        case._scenario_test_schematized_operation_name_v1()
